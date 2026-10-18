/* Actual Darling dirfd, symlink access, bounded path and per-thread cwd regression. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
int main(void) {
    char dir[] = "/tmp/dearust-faccessat-XXXXXX"; assert(mkdtemp(dir));
    char dest[256]; snprintf(dest, sizeof(dest), "%s/dest", dir);
    assert(mkdir(dest, 0700) == 0);
    int fd = open(dest, O_RDONLY); assert(fd >= 0 && chdir(dest) == 0);
    int out = open("target", O_CREAT | O_EXCL | O_WRONLY, 0600); assert(out >= 0);
    assert(write(out, "kept", 4) == 4 && close(out) == 0);
    assert(symlink("target", "link") == 0 && symlink("missing", "dangling") == 0 && chdir(dir) == 0);
    assert(faccessat(fd, "target", F_OK, 0) == 0);
    assert(faccessat(fd, "target", R_OK | W_OK, 0) == 0);
    errno = 0; assert(faccessat(AT_FDCWD, "target", F_OK, 0) == -1 && errno == ENOENT);
    errno = 0; assert(faccessat(fd, "target", -1, 0) == -1 && errno == EINVAL);
    errno = 0; assert(faccessat(fd, "missing", F_OK, 0) == -1 && errno == ENOENT);
    assert(faccessat(fd, "link", F_OK, 0) == 0);
    assert(faccessat(fd, "link", F_OK, AT_SYMLINK_NOFOLLOW) == 0);
    assert(faccessat(fd, "dangling", F_OK, AT_SYMLINK_NOFOLLOW) == 0);
    assert(faccessat(fd, "target", R_OK | W_OK, AT_EACCESS) == 0);
    assert(faccessat(fd, "dangling", F_OK, AT_EACCESS | AT_SYMLINK_NOFOLLOW) == 0);
    errno = 0; assert(faccessat(fd, "target", F_OK, (int)0x80000000) == -1 && errno == EINVAL);
    errno = 0; assert(faccessat(fd, NULL, F_OK, 0) == -1 && errno == EFAULT);
    char longpath[4097]; memset(longpath, 'x', sizeof(longpath));
    errno = 0; assert(faccessat(fd, longpath, F_OK, 0) == -1 && errno == ENAMETOOLONG);
    longpath[4096] = 0;
    errno = 0; assert(faccessat(fd, longpath, F_OK, 0) == -1 && errno == ENAMETOOLONG);
    assert(pthread_fchdir_np(fd) == 0 && faccessat(AT_FDCWD, "target", R_OK, 0) == 0);
    assert(pthread_fchdir_np(-1) == 0);
    out = openat(fd, "target", O_RDONLY); char data[4];
    assert(out >= 0 && read(out, data, 4) == 4 && memcmp(data, "kept", 4) == 0 && close(out) == 0);
    assert(unlinkat(fd, "link", 0) == 0 && unlinkat(fd, "dangling", 0) == 0 && unlinkat(fd, "target", 0) == 0);
    assert(close(fd) == 0 && chdir("/") == 0 && rmdir(dest) == 0 && rmdir(dir) == 0);
    puts("Rust faccessat regression checks passed");
}
