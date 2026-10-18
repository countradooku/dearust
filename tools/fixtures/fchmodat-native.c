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
extern long sys_fchmodat(int, const char *, int, int);
extern int do_linux_fchmodat(int, const char *, int, int);
static int cwd_fd, expect_fd, expect_follow = 1, phase, expansion_error;
static int calls, forced_error, recording, swap_link;
int atfd(int fd) { assert(phase == 0); phase = 1; return fd == -2 ? cwd_fd : fd; }
int vchroot_expand(struct expanded *args) {
    assert(phase == 1); phase = 2;
    assert(args->dfd == expect_fd && args->flags == (unsigned)expect_follow);
    assert(memchr(args->path, 0, sizeof(args->path)));
    return expansion_error;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    long memory_result;
    if (dearust_native_memory_syscall(a, b, c, d, e, f, nr, &memory_result)) return memory_result;
    assert(phase == 2 || phase == 0);
    assert(e == 0 && f == 0);
    /* No newfstatat is permitted: there must be no check/use race. */
    assert(nr == (d == 0 ? SYS_fchmodat : 452)); calls++;
    if (forced_error) return forced_error;
    if (recording) {
        assert(a == -42 && c == -123 && b && strcmp((const char *)b, "recorded") == 0);
        return 0;
    }
    if (swap_link) {
        swap_link = 0;
        assert(unlinkat((int)a, (const char *)b, 0) == 0);
        assert(symlinkat("target", (int)a, (const char *)b) == 0);
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
    int before = calls;
    assert(call(dfd, "link", 0777, 0x20) == -102 && calls == before + 1);
    assert(permissions(dfd, "target") == 0640);
    assert(call(dfd, "target", 0604, 0x20) == 0 && permissions(dfd, "target") == 0604);
    assert(call(-2, "target", 0600, 0) == 0);
    assert(call(dfd, "missing", 0600, 0x20) == -2);
    phase = 0; assert(do_linux_fchmodat(dfd, "missing", 0600, 0x100) == -ENOENT);
    assert(do_linux_fchmodat(dfd, NULL, 0600, 0x100) == -EFAULT);
    assert(do_linux_fchmodat(dfd, NULL, 0600, 0) == -EFAULT);
    before = calls;
    assert(call(dfd, "target", 0600, (int)0x80000000) == -22 && phase == 0 && calls == before);
    assert(call(dfd, "target", 0600, 0x10) == -22 && phase == 0);
    expansion_error = -20;
    assert(call(dfd, "missing", 0600, 0) == -20 && phase == 2);
    expansion_error = 0;
    assert(call(dfd, NULL, 0600, 0) == -14 && phase == 0);
    char longpath[4096]; memset(longpath, 'x', sizeof(longpath));
    assert(call(dfd, longpath, 0600, 0) == -63 && phase == 1);
    phase = 0; forced_error = -ENOSYS; before = calls;
    assert(do_linux_fchmodat(dfd, "target", 0777, 0x100) == -ENOSYS && calls == before + 1);
    assert(call(dfd, "target", 0777, 0x20) == -78 && calls == before + 2);
    assert(permissions(dfd, "target") == 0600);
    forced_error = -EOPNOTSUPP;
    assert(call(dfd, "target", 0600, 0x20) == -102);
    forced_error = 0; recording = 1; phase = 0;
    assert(do_linux_fchmodat(-42, "recorded", -123, 0) == 0);
    assert(do_linux_fchmodat(-42, "recorded", -123, 0x100) == 0);
    recording = 0;
    /* Replace a regular file with a symlink exactly at the kernel boundary.
       NOFOLLOW must not mutate target and there is no prior stat result to race. */
    fd = openat(dfd, "swapped", O_CREAT | O_WRONLY, 0600); assert(fd >= 0); close(fd);
    swap_link = 1; assert(call(dfd, "swapped", 0777, 0x20) == -102);
    assert(permissions(dfd, "target") == 0600);
    assert(unlinkat(dfd, "swapped", 0) == 0);
    assert(unlinkat(dfd, "link", 0) == 0 && unlinkat(dfd, "target", 0) == 0);
    assert(close(dfd) == 0 && rmdir(directory) == 0);
    puts("fchmodat: atomic no-follow, truthful symlink/ENOSYS errors, legacy fast path and bounds");
}
