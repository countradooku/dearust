/* Native adapters; actual kernel output validation, guest vchroot tested separately. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
struct expanded { char path[4096]; unsigned flags; int dfd; };
extern long sys_readlinkat(int, const char *, char *, int);
static int current_wd = AT_FDCWD, expected_fd, expanded_calls, expansion_error;
static int expected_count;
static char *expected_output;
int atfd(int fd) { return fd == -2 ? current_wd : fd; }
int vchroot_expand(struct expanded *args) {
    assert(args->dfd == expected_fd && args->flags == 0);
    assert(memchr(args->path, 0, sizeof(args->path)));
    expanded_calls++;
    return expansion_error;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(nr == SYS_readlinkat && e == 0 && f == 0);
    assert(c == (long)expected_output && d == (long)expected_count);
    long result = syscall(nr, a, b, c, d, e, f);
    return result < 0 ? -errno : result;
}
static long call(int fd, const char *path, char *out, int count) {
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
    assert(call(dfd, "link", bytes, 0) == -22);
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
