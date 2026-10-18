/* Darling GPL-3.0-or-later access/chmod ABI; canonical wrapper delegates. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "guest-memory-native.h"
struct expanded { char path[4096]; unsigned flags; int dfd; };
extern long sys_access(const char *, int);
extern long sys_chmod(const char *, int);
static int directory_fd, phase, expected_nr, expected_mode, calls;
int dearust_get_perthread_wd(void) { assert(phase == 0); phase = 1; return directory_fd; }
int atfd(int fd) { assert(phase == 1 && fd == directory_fd); phase = 2; return fd; }
int vchroot_expand(struct expanded *p) {
    assert(phase == 2 && p->dfd == directory_fd && p->flags == 1);
    assert(memchr(p->path, 0, sizeof(p->path))); phase = 3; return 0;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    long memory_result;
    if (dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;
    assert(phase == 3 && nr == expected_nr && c == expected_mode);
    assert(a == directory_fd && b && d == 0 && e == 0 && f == 0); calls++;
    long result = syscall(nr,a,b,c,d,e,f); return result < 0 ? -errno : result;
}
static long access_call(const char *path, int mode) {
    phase = 0; expected_nr = SYS_faccessat; expected_mode = mode;
    return sys_access(path, mode);
}
static long chmod_call(const char *path, int mode) {
    phase = 0; expected_nr = SYS_fchmodat; expected_mode = mode;
    return sys_chmod(path, mode);
}
int main(void) {
    char path[] = "/tmp/dearust-permissions-XXXXXX"; assert(mkdtemp(path));
    directory_fd = open(path, O_RDONLY | O_DIRECTORY); assert(directory_fd >= 0);
    int fd = openat(directory_fd, "target", O_WRONLY | O_CREAT | O_EXCL, 0600);
    assert(fd >= 0 && close(fd) == 0);
    assert(access_call("target", R_OK | W_OK) == 0 && phase == 3);
    assert(chmod_call("target", 0640) == 0 && phase == 3);
    struct stat st; assert(fstatat(directory_fd, "target", &st, 0) == 0 && (st.st_mode & 0777) == 0640);
    assert(access_call("missing", F_OK) == -2 && chmod_call("missing", 0600) == -2);
    assert(access_call("target", -1) == -22);
    /* The wrapper must preserve the full signed C int mode argument. */
    assert(chmod_call("target", -1) == 0);
    int before = calls;
    assert(access_call(NULL, F_OK) == -14 && phase == 1 && calls == before);
    assert(chmod_call(NULL, 0600) == -14 && phase == 1 && calls == before);
    char overlong[4096]; memset(overlong, 'x', sizeof(overlong));
    assert(access_call(overlong, F_OK) == -63 && phase == 2 && calls == before);
    assert(chmod_call(overlong, 0600) == -63 && phase == 2 && calls == before);
    assert(unlinkat(directory_fd, "target", 0) == 0 && close(directory_fd) == 0 && rmdir(path) == 0);
    puts("permissions: TLS-first access/chmod delegates, signed modes, real filesystem and errors");
}
