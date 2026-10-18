/* Native adapters; actual kernel output validation, guest vchroot tested separately. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "guest-memory-native.h"
struct expanded { char path[4096]; unsigned flags; int dfd; };
extern long sys_readlinkat(int, const char *, char *, size_t);
static int current_wd = AT_FDCWD, expected_fd, expanded_calls, expansion_error;
static size_t expected_count;
static char *expected_output;
int atfd(int fd) { return fd == -2 ? current_wd : fd; }
int vchroot_expand(struct expanded *args) {
    assert(args->dfd == expected_fd && args->flags == 0);
    assert(memchr(args->path, 0, sizeof(args->path)));
    expanded_calls++;
    return expansion_error;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    long memory_result; if(dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;
    assert(nr == SYS_readlinkat && e == 0 && f == 0);
    if(expected_count==0) {
        assert(c!=(long)expected_output && c!=0 && d==1);
        assert(*(const unsigned char*)c==0);
    } else assert(c == (long)expected_output && d == (long)expected_count);
    long result = syscall(nr, a, b, c, d, e, f);
    return result < 0 ? -errno : result;
}
static long call(int fd, const char *path, char *out, size_t count) {
    expected_output = out; expected_count = count;
    return sys_readlinkat(fd, path, out, count);
}
int main(void) {
    char directory[] = "/tmp/dearust-readlinkat-XXXXXX";
    assert(mkdtemp(directory));
    int dfd = open(directory, O_RDONLY | O_DIRECTORY); assert(dfd >= 0);
    expected_fd = dfd;
    const char *target = "../missing/\xff-target";
    assert(symlinkat(target, dfd, "link") == 0);
    char bytes[128]; memset(bytes, 0x55, sizeof(bytes));
    assert(call(dfd, "link", bytes, sizeof(bytes)) == (long)strlen(target));
    assert(memcmp(bytes, target, strlen(target)) == 0 && bytes[strlen(target)] == 0x55);
    memset(bytes, 0x55, sizeof(bytes));
    assert(call(dfd, "link", bytes, 3) == 3);
    assert(memcmp(bytes, target, 3) == 0 && bytes[3] == 0x55);
    current_wd = dfd;
    assert(call(-2, "link", bytes, sizeof(bytes)) == (long)strlen(target));
    assert(call(dfd, "link", bytes, 0) == 0);
    assert(call(dfd, "link", NULL, 0) == 0);
    assert(call(dfd, "missing", NULL, 0) == -2);
    const size_t oversized[]={UINT64_C(0x80000000),UINT64_C(0xffffffff),UINT64_C(0x100000001),SIZE_MAX};
    for(unsigned i=0;i<sizeof(oversized)/sizeof(oversized[0]);i++) {
        int before=expanded_calls;
        assert(call(dfd, NULL, (char*)1, oversized[i]) == -22);
        assert(expanded_calls==before);
    }
    assert(call(dfd, "link", bytes, -1) == -22);
    assert(call(dfd, "link", NULL, sizeof(bytes)) == -14);
    assert(call(dfd, "missing", NULL, sizeof(bytes)) == -2);
    expanded_calls = 0;
    assert(call(dfd, NULL, bytes, sizeof(bytes)) == -14);
    char path[4096]; memset(path, 'x', sizeof(path));
    assert(call(dfd, path, bytes, sizeof(bytes)) == -63 && expanded_calls == 0);
    expansion_error = -36;
    assert(call(dfd, "link", NULL, sizeof(bytes)) == -63);
    expansion_error = 0;
    expected_fd = -42;
    assert(call(-42, "link", bytes, sizeof(bytes)) == -9);
    assert(unlinkat(dfd, "link", 0) == 0 && close(dfd) == 0 && rmdir(directory) == 0);
    puts("readlinkat: dirfd, cwd, raw bytes/counts, untouched tail, bounded path and kernel output errors");
}
