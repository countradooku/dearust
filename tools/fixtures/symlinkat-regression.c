/* Actual Darling dirfd/vchroot/thread-cwd and symlink text regression. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
static void check_link(int dfd, const char *name, const char *target) {
    char bytes[128];
    ssize_t count = readlinkat(dfd, name, bytes, sizeof(bytes));
    assert(count == (ssize_t)strlen(target) && memcmp(bytes, target, (size_t)count) == 0);
}
int main(void) {
    char directory[] = "/tmp/dearust-symlinkat-XXXXXX";
    assert(mkdtemp(directory));
    char dest[256]; snprintf(dest, sizeof(dest), "%s/dest", directory);
    assert(mkdir(dest, 0700) == 0);
    int cwd = open(directory, O_RDONLY), dfd = open(dest, O_RDONLY);
    assert(cwd >= 0 && dfd >= 0 && chdir(directory) == 0);
    const char *target = "../missing/\xff-target";
    assert(symlinkat(target, dfd, "link") == 0);
    /* Original ignores dfd and creates cwd/link instead of dest/link. */
    check_link(dfd, "link", target);
    struct stat st;
    errno = 0; assert(lstat("link", &st) == -1 && errno == ENOENT);
    errno = 0; assert(symlinkat(target, dfd, "link") == -1 && errno == EEXIST);
    assert(symlinkat(target, AT_FDCWD, "cwd-link") == 0);
    check_link(cwd, "cwd-link", target);
    assert(pthread_fchdir_np(dfd) == 0);
    assert(symlinkat(target, AT_FDCWD, "thread-link") == 0);
    check_link(dfd, "thread-link", target);
    assert(pthread_fchdir_np(-1) == 0);
    errno = 0; assert(symlinkat(target, dfd, NULL) == -1 && errno == EFAULT);
    errno = 0; assert(symlinkat(NULL, dfd, "bad-target") == -1 && errno == EFAULT);
    char longpath[4097]; memset(longpath, 'x', sizeof(longpath));
    errno = 0; assert(symlinkat(target, dfd, longpath) == -1 && errno == ENAMETOOLONG);
    longpath[4096] = 0;
    errno = 0; assert(symlinkat(target, dfd, longpath) == -1 && errno == ENAMETOOLONG);
    errno = 0; assert(fstatat(dfd, "bad-target", &st, AT_SYMLINK_NOFOLLOW) == -1 && errno == ENOENT);
    assert(unlinkat(dfd, "link", 0) == 0 && unlinkat(dfd, "thread-link", 0) == 0);
    assert(unlink("cwd-link") == 0 && chdir("/") == 0);
    assert(close(dfd) == 0 && close(cwd) == 0);
    assert(rmdir(dest) == 0 && rmdir(directory) == 0);
    puts("Rust symlinkat regression checks passed");
    return 0;
}
