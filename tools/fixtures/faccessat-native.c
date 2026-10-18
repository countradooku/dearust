/* Darling GPL-3.0-or-later ABI; explicit adapters and real Linux calls. */
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
extern long sys_faccessat(int, const char *, int, int);
static int cwd_fd, expect_fd, expansion_error, expansions, calls, forced_error;
static unsigned expect_follow = 1;
static int expected_mode, expected_flags;
int atfd(int fd) { return fd == -2 ? cwd_fd : fd; }
int vchroot_expand(struct expanded *p) {
    assert(p->dfd == expect_fd && p->flags == expect_follow);
    assert(memchr(p->path, 0, sizeof(p->path))); expansions++;
    return expansion_error;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    long memory_result;
    if (dearust_native_memory_syscall(a, b, c, d, e, f, nr, &memory_result)) return memory_result;
    assert(nr == (expected_flags == 0 ? SYS_faccessat : 439));
    assert(c == expected_mode && d == expected_flags && e == 0 && f == 0);
    calls++; if (forced_error) return forced_error;
    long result = syscall(nr, a, b, c, d);
    return result < 0 ? -errno : result;
}
int main(void) {
    char dir[] = "/tmp/dearust-faccessat-XXXXXX"; assert(mkdtemp(dir));
    int fd = open(dir, O_RDONLY | O_DIRECTORY); assert(fd >= 0); expect_fd = fd;
    int out = openat(fd, "target", O_CREAT | O_EXCL | O_WRONLY, 0600); assert(out >= 0);
    assert(write(out, "kept", 4) == 4 && close(out) == 0);
    assert(sys_faccessat(fd, "target", F_OK, 0) == 0);
    expected_mode = R_OK | W_OK; assert(sys_faccessat(fd, "target", expected_mode, 0) == 0);
    expected_mode = -1; assert(sys_faccessat(fd, "target", -1, 0) == -22);
    expected_mode = 0; assert(sys_faccessat(fd, "missing", 0, 0) == -2);
    assert(symlinkat("target", fd, "link") == 0 && symlinkat("missing", fd, "dangling") == 0);
    assert(sys_faccessat(fd, "link", 0, 0) == 0);
    assert(sys_faccessat(fd, "dangling", 0, 0) == -2);
    expect_follow = 0; expected_flags = AT_SYMLINK_NOFOLLOW;
    assert(sys_faccessat(fd, "link", 0, 0x20) == 0);
    assert(sys_faccessat(fd, "dangling", 0, 0x20) == 0);
    expected_flags |= AT_EACCESS;
    assert(sys_faccessat(fd, "dangling", 0, 0x30) == 0);
    expect_follow = 1; expected_flags = AT_EACCESS;
    assert(sys_faccessat(fd, "target", 0, 0x10) == 0);
    int before = calls;
    forced_error = -ENOSYS;
    assert(sys_faccessat(fd, "target", 0, 0x10) == -78 && calls == before + 1);
    forced_error = -EOPNOTSUPP;
    assert(sys_faccessat(fd, "target", 0, 0x10) == -102 && calls == before + 2);
    forced_error = 0; before = calls; int expanded = expansions;
    assert(sys_faccessat(fd, "target", 0, (int)0x80000000) == -22 && calls == before && expansions == expanded);
    assert(sys_faccessat(fd, "target", 0, 0x200) == -22 && calls == before);
    expansion_error = -13;
    assert(sys_faccessat(fd, "target", 0, 0x10) == -13 && calls == before);
    expansion_error = 0; before = expansions;
    assert(sys_faccessat(fd, NULL, 0, 0) == -14);
    char longpath[4096]; memset(longpath, 'x', sizeof(longpath));
    assert(sys_faccessat(fd, longpath, 0, 0) == -63 && expansions == before);
    expected_flags = 0; expect_fd = -42; assert(sys_faccessat(-42, "target", 0, 0) == -9);
    expected_flags = AT_EACCESS; expect_fd = fd; cwd_fd = fd;
    assert(sys_faccessat(-2, "target", 0, 0x10) == 0);
    out = openat(fd, "target", O_RDONLY); char data[4];
    assert(out >= 0 && read(out, data, 4) == 4 && memcmp(data, "kept", 4) == 0 && close(out) == 0);
    assert(unlinkat(fd, "link", 0) == 0 && unlinkat(fd, "dangling", 0) == 0 && unlinkat(fd, "target", 0) == 0);
    assert(close(fd) == 0 && rmdir(dir) == 0);
    puts("faccessat: effective-ID/no-follow flags, legacy fast path, ENOSYS, dirfd/TLS and bounds");
}
