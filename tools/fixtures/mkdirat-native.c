/* Original Darling GPL-3.0-or-later ABI with explicit adapters and real Linux syscall. */
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
extern long sys_mkdirat(int, const char *, unsigned);
static int cwd_fd, expect_fd, expansion_error, expansions, calls;
static unsigned expected_mode;
int atfd(int fd) { return fd == -2 ? cwd_fd : fd; }
int vchroot_expand(struct expanded *p) {
    assert(p->dfd == expect_fd && p->flags == 0);
    assert(memchr(p->path, 0, sizeof(p->path))); expansions++;
    return expansion_error;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(nr == SYS_mkdirat && c == (long)expected_mode && d == 0 && e == 0 && f == 0);
    calls++; long result = syscall(nr, a, b, c);
    return result < 0 ? -errno : result;
}
int main(void) {
    char dir[] = "/tmp/dearust-mkdirat-XXXXXX"; assert(mkdtemp(dir));
    int fd = open(dir, O_RDONLY | O_DIRECTORY); assert(fd >= 0); expect_fd = fd;
    mode_t mask = umask(0027); expected_mode = 0777;
    assert(sys_mkdirat(fd, "made", expected_mode) == 0);
    struct stat st; assert(fstatat(fd, "made", &st, 0) == 0 && (st.st_mode & 0777) == 0750);
    assert(sys_mkdirat(fd, "made", expected_mode) == -17);
    assert(sys_mkdirat(fd, "missing/child", expected_mode) == -2);
    assert(symlinkat("made", fd, "link") == 0);
    assert(sys_mkdirat(fd, "link", expected_mode) == -17);
    int before = calls; expansion_error = -13;
    assert(sys_mkdirat(fd, "other", expected_mode) == -13 && calls == before);
    expansion_error = 0; before = expansions;
    assert(sys_mkdirat(fd, NULL, expected_mode) == -14);
    char longpath[4096]; memset(longpath, 'x', sizeof(longpath));
    assert(sys_mkdirat(fd, longpath, expected_mode) == -63 && expansions == before);
    expect_fd = -42; assert(sys_mkdirat(-42, "bad", expected_mode) == -9);
    expect_fd = fd; cwd_fd = fd; expected_mode = 0xffffffffU;
    assert(sys_mkdirat(-2, "thread", expected_mode) == 0);
    assert(unlinkat(fd, "thread", AT_REMOVEDIR) == 0);
    assert(unlinkat(fd, "link", 0) == 0 && unlinkat(fd, "made", AT_REMOVEDIR) == 0);
    umask(mask); assert(close(fd) == 0 && rmdir(dir) == 0);
    puts("mkdirat: real mkdir, modes/umask, dirfd/TLS, errors and bounded paths");
}
