/* Original Darling GPL-3.0-or-later ABI, explicit adapters and real Linux syscall. */
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
struct expanded { char path[4096]; unsigned flags; int dfd; };
extern long sys_unlinkat(int, const char *, int);
static int cwd_fd = AT_FDCWD, expect_fd, expansion_error, expanded_calls, flag_calls, syscall_calls;
int atfd(int fd) { return fd == -2 ? cwd_fd : fd; }
int vchroot_expand(struct expanded *args) {
    assert(args->dfd == expect_fd && args->flags == 0);
    assert(memchr(args->path, 0, sizeof(args->path)));
    expanded_calls++;
    return expansion_error;
}
int atflags_bsd_to_linux(int flags) {
    assert(expanded_calls > flag_calls);
    flag_calls++;
    int result = 0;
    if (flags & 0x20) result |= AT_SYMLINK_NOFOLLOW;
    if (flags & (0x80 | 0x100)) result |= AT_REMOVEDIR;
    if (flags & 0x40) result |= AT_SYMLINK_FOLLOW;
    if (flags & 0x400) result |= AT_EMPTY_PATH;
    return flags & ~(0x20 | 0x80 | 0x100 | 0x40 | 0x10 | 0x200 | 0x400) ? 1 : result;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(nr == SYS_unlinkat && d == 0 && e == 0 && f == 0);
    syscall_calls++;
    long result = syscall(nr, a, b, c, d, e, f);
    return result < 0 ? -errno : result;
}
static void touch(int fd, const char *name) {
    int out = openat(fd, name, O_CREAT | O_EXCL | O_WRONLY, 0600);
    assert(out >= 0 && close(out) == 0);
}
static void exists(int fd, const char *name) { struct stat st; assert(fstatat(fd, name, &st, 0) == 0); }
int main(void) {
    char directory[] = "/tmp/dearust-unlinkat-XXXXXX";
    assert(mkdtemp(directory));
    int dfd = open(directory, O_RDONLY | O_DIRECTORY); assert(dfd >= 0);
    expect_fd = dfd;
    touch(dfd, "target");
    assert(symlinkat("target", dfd, "link") == 0);
    assert(sys_unlinkat(dfd, "link", 0) == 0); exists(dfd, "target");
    assert(mkdirat(dfd, "sub", 0700) == 0);
    assert(sys_unlinkat(dfd, "sub", 0x80) == 0);
    assert(mkdirat(dfd, "sub", 0700) == 0);
    assert(sys_unlinkat(dfd, "sub", 0x100) == 0);
    int calls = syscall_calls;
    assert(sys_unlinkat(dfd, "target", 0x80000000) == -22);
    assert(syscall_calls == calls); exists(dfd, "target");
    expansion_error = -2; int conversions = flag_calls;
    assert(sys_unlinkat(dfd, "missing", 0x80000000) == -2);
    assert(flag_calls == conversions && syscall_calls == calls);
    expansion_error = 0;
    assert(sys_unlinkat(dfd, "target", 0x20) == -22); exists(dfd, "target");
    int expansions = expanded_calls;
    assert(sys_unlinkat(dfd, NULL, 0) == -14);
    char longpath[4096]; memset(longpath, 'x', sizeof(longpath));
    assert(sys_unlinkat(dfd, longpath, 0) == -63);
    assert(expanded_calls == expansions); exists(dfd, "target");
    expect_fd = -42;
    assert(sys_unlinkat(-42, "target", 0) == -9);
    expect_fd = dfd; cwd_fd = dfd;
    assert(sys_unlinkat(-2, "target", 0x10 | 0x200) == 0);
    assert(close(dfd) == 0 && rmdir(directory) == 0);
    puts("unlinkat: real deletion, nofollow, dirfd/TLS, flags, ordering and bounded paths");
}
