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
struct expanded { char path[4096]; unsigned flags; int dfd; };
extern long sys_fchmodat(int, const char *, int, int);
extern int do_linux_fchmodat(int, const char *, int, int);
static int cwd_fd, expect_fd, expect_follow = 1, phase, expansion_error;
static int stat_calls, chmod_calls, forced_error;
static unsigned fake_type;
static int recording;
int atfd(int fd) { assert(phase == 0); phase = 1; return fd == -2 ? cwd_fd : fd; }
int vchroot_expand(struct expanded *args) {
    assert(phase == 1); phase = 2;
    assert(args->dfd == expect_fd && args->flags == (unsigned)expect_follow);
    assert(memchr(args->path, 0, sizeof(args->path)));
    return expansion_error;
}
int atflags_bsd_to_linux(int flags) {
    assert(phase == 2); phase = 3;
    int result = 0;
    if (flags & 0x20) result |= AT_SYMLINK_NOFOLLOW;
    if (flags & (0x80 | 0x100)) result |= AT_REMOVEDIR;
    if (flags & 0x40) result |= AT_SYMLINK_FOLLOW;
    if (flags & 0x400) result |= AT_EMPTY_PATH;
    return flags & ~(0x20 | 0x80 | 0x100 | 0x40 | 0x10 | 0x200 | 0x400) ? 1 : result;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(phase == 3 || phase == 0);
    assert(e == 0 && f == 0);
    if (nr == SYS_newfstatat) {
        stat_calls++;
        assert(d == (AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW));
        assert(c && !(c & 7));
        if (forced_error) return forced_error;
        if (recording) {
            assert(a == -42 && b && strcmp((const char *)b, "recorded") == 0);
            memset((void *)c, 0, 144); memcpy((char *)c + 24, &fake_type, 4); return 0;
        }
    } else {
        assert(nr == SYS_fchmodat && d == 0); chmod_calls++;
        if (forced_error) return forced_error;
        if (recording) { assert(a == -42 && c == -123 && b); return 0; }
    }
    long result = syscall(nr, a, b, c, d, e, f);
    return result < 0 ? -errno : result;
}
static long call(int fd, const char *path, int mode, int flags) {
    phase = 0; expect_follow = (flags & 0x20) ? 0 : 1;
    return sys_fchmodat(fd, path, mode, flags);
}
static unsigned permissions(int fd, const char *name) {
    struct stat st; assert(fstatat(fd, name, &st, 0) == 0); return st.st_mode & 0777;
}
int main(void) {
    char directory[] = "/tmp/dearust-fchmodat-XXXXXX"; assert(mkdtemp(directory));
    int dfd = open(directory, O_RDONLY | O_DIRECTORY); assert(dfd >= 0);
    int fd = openat(dfd, "target", O_CREAT | O_WRONLY, 0600); assert(fd >= 0); close(fd);
    expect_fd = cwd_fd = dfd;
    assert(call(dfd, "target", 0640, 0) == 0 && permissions(dfd, "target") == 0640);
    assert(symlinkat("target", dfd, "link") == 0);
    int calls = chmod_calls;
    assert(call(dfd, "link", 0777, 0x20) == 0 && chmod_calls == calls);
    assert(permissions(dfd, "target") == 0640);
    assert(call(dfd, "target", 0604, 0x20) == 0 && permissions(dfd, "target") == 0604);
    assert(call(-2, "target", 0600, 0x10 | 0x200) == 0);
    assert(call(dfd, "missing", 0600, 0x20) == -2);
    phase = 0; assert(do_linux_fchmodat(dfd, "missing", 0600, 1) == -ENOENT);
    assert(do_linux_fchmodat(dfd, NULL, 0600, 1) == -EFAULT);
    assert(do_linux_fchmodat(dfd, NULL, 0600, 0) == -EFAULT);
    assert(call(dfd, "target", 0600, (int)0x80000000) == -22 && phase == 3);
    expansion_error = -20;
    assert(call(dfd, "missing", 0600, (int)0x80000000) == -20 && phase == 2);
    expansion_error = 0;
    assert(call(dfd, NULL, 0600, 0) == -14 && phase == 0);
    char longpath[4096]; memset(longpath, 'x', sizeof(longpath));
    assert(call(dfd, longpath, 0600, 0) == -63 && phase == 1);
    phase = 0; forced_error = -95;
    assert(do_linux_fchmodat(dfd, "target", 0600, 0x100) == -95);
    assert(call(dfd, "target", 0600, 0x20) == -102);
    forced_error = 0; recording = 1; fake_type = S_IFREG; phase = 0;
    assert(do_linux_fchmodat(-42, "recorded", -123, 0x40000000) == 0);
    fake_type = S_IFLNK; calls = chmod_calls;
    assert(do_linux_fchmodat(-42, "recorded", -123, 1) == 0 && chmod_calls == calls);
    assert(stat_calls > 0); recording = 0;
    assert(unlinkat(dfd, "link", 0) == 0 && unlinkat(dfd, "target", 0) == 0);
    assert(close(dfd) == 0 && rmdir(directory) == 0);
    puts("fchmodat: helper/wrapper errno, raw ABI, mode changes, symlink no-op, TLS and bounded paths");
}
