/* Actual Darling dirfd, no-follow expansion and per-thread cwd regression. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
static void touch(const char *path) {
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    assert(fd >= 0 && close(fd) == 0);
}
static void exists(const char *path) { struct stat st; assert(stat(path, &st) == 0); }
int main(void) {
    char directory[] = "/tmp/dearust-unlinkat-XXXXXX";
    assert(mkdtemp(directory));
    char dest[256]; snprintf(dest, sizeof(dest), "%s/dest", directory);
    assert(mkdir(dest, 0700) == 0 && chdir(dest) == 0);
    touch("target"); touch("victim"); touch("thread");
    assert(symlink("target", "link") == 0 && mkdir("sub", 0700) == 0);
    int dfd = open(dest, O_RDONLY); assert(dfd >= 0);
    assert(chdir(directory) == 0);
    touch("victim"); touch("thread");
    assert(unlinkat(dfd, "victim", 0) == 0); exists("victim");
    assert(unlinkat(dfd, "link", 0) == 0);
    assert(unlinkat(dfd, "sub", AT_REMOVEDIR) == 0);
    errno = 0;
    assert(unlinkat(dfd, "target", (int)0x80000000) == -1 && errno == EINVAL);
    errno = 0;
    assert(unlinkat(dfd, "no-parent/file", (int)0x80000000) == -1 && errno == EINVAL);
    errno = 0;
    assert(unlinkat(dfd, "target", AT_SYMLINK_NOFOLLOW) == -1 && errno == EINVAL);
    errno = 0;
    assert(unlinkat(dfd, NULL, 0) == -1 && errno == EFAULT);
    char longpath[4097]; memset(longpath, 'x', sizeof(longpath));
    errno = 0;
    assert(unlinkat(dfd, longpath, 0) == -1 && errno == ENAMETOOLONG);
    longpath[4096] = 0;
    errno = 0;
    assert(unlinkat(dfd, longpath, 0) == -1 && errno == ENAMETOOLONG);
    assert(pthread_fchdir_np(dfd) == 0);
    assert(unlinkat(AT_FDCWD, "thread", 0) == 0);
    assert(pthread_fchdir_np(-1) == 0); exists("thread");
    assert(chdir(dest) == 0); exists("target");
    struct stat st;
    errno = 0; assert(lstat("link", &st) == -1 && errno == ENOENT);
    errno = 0; assert(lstat("victim", &st) == -1 && errno == ENOENT);
    errno = 0; assert(lstat("thread", &st) == -1 && errno == ENOENT);
    assert(unlink("target") == 0 && chdir(directory) == 0);
    assert(unlink("thread") == 0 && unlink("victim") == 0);
    assert(close(dfd) == 0 && chdir("/") == 0);
    assert(rmdir(dest) == 0 && rmdir(directory) == 0);
    puts("Rust unlinkat regression checks passed");
    return 0;
}
