/* Native boundary adapters; guest tests separately validate virtual root/TLS. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
struct expanded { char path[4096]; unsigned flags; int dfd; };
extern long sys_symlinkat(const char *, int, const char *);
static int current_wd = AT_FDCWD, expected_fd, expanded_calls, expansion_error;
static const char *expected_target;
int atfd(int fd) { return fd == -2 ? current_wd : fd; }
int vchroot_expand(struct expanded *args) {
    assert(args->dfd == expected_fd && args->flags == 0);
    assert(memchr(args->path, 0, sizeof(args->path)));
    expanded_calls++;
    return expansion_error;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(nr == SYS_symlinkat && d == 0 && e == 0 && f == 0);
    assert((const char *)a == expected_target);
    long result = syscall(nr, a, b, c, d, e, f);
    return result < 0 ? -errno : result;
}
static void check_link(int dfd, const char *name, const char *target) {
    char bytes[128];
    ssize_t count = readlinkat(dfd, name, bytes, sizeof(bytes));
    assert(count == (ssize_t)strlen(target) && memcmp(bytes, target, (size_t)count) == 0);
}
int main(void) {
    char directory[] = "/tmp/dearust-symlinkat-XXXXXX";
    assert(mkdtemp(directory));
    int dfd = open(directory, O_RDONLY | O_DIRECTORY); assert(dfd >= 0);
    expected_fd = dfd;
    expected_target = "../missing/\xff-target";
    assert(sys_symlinkat(expected_target, dfd, "link") == 0);
    check_link(dfd, "link", expected_target);
    assert(sys_symlinkat(expected_target, dfd, "link") == -17);
    current_wd = dfd;
    assert(sys_symlinkat(expected_target, -2, "cwd-link") == 0);
    check_link(dfd, "cwd-link", expected_target);
    expanded_calls = 0;
    assert(sys_symlinkat(expected_target, dfd, NULL) == -14);
    char path[4096]; memset(path, 'x', sizeof(path));
    assert(sys_symlinkat(expected_target, dfd, path) == -63);
    assert(expanded_calls == 0);
    expansion_error = -36;
    assert(sys_symlinkat(NULL, dfd, "bad-target") == -63);
    expansion_error = 0; expected_target = NULL;
    assert(sys_symlinkat(NULL, dfd, "bad-target") == -14);
    struct stat st;
    assert(fstatat(dfd, "bad-target", &st, AT_SYMLINK_NOFOLLOW) == -1 && errno == ENOENT);
    expected_fd = -42; expected_target = "unchanged";
    assert(sys_symlinkat(expected_target, -42, "bad-fd") == -9);
    current_wd = AT_FDCWD; expected_fd = AT_FDCWD;
    char absolute[256]; snprintf(absolute, sizeof(absolute), "%s/absolute", directory);
    assert(sys_symlinkat(expected_target, -2, absolute) == 0);
    check_link(dfd, "absolute", expected_target);
    assert(unlinkat(dfd, "link", 0) == 0 && unlinkat(dfd, "cwd-link", 0) == 0);
    assert(unlinkat(dfd, "absolute", 0) == 0 && close(dfd) == 0 && rmdir(directory) == 0);
    puts("symlinkat: dirfd, cwd, unchanged target bytes, bounded destination, expansion ordering and errno");
}
