/* Actual Darling no-follow expansion, dirfd/thread cwd and output contracts. */
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
    char directory[] = "/tmp/dearust-readlinkat-XXXXXX";
    assert(mkdtemp(directory));
    char dest[256]; snprintf(dest, sizeof(dest), "%s/dest", directory);
    assert(mkdir(dest, 0700) == 0);
    int cwd = open(directory, O_RDONLY), dfd = open(dest, O_RDONLY);
    assert(cwd >= 0 && dfd >= 0 && chdir(directory) == 0);
    const char *target = "../missing/\xff-target";
    /* symlink uses the same directory only to construct the input fixture. */
    assert(chdir(dest) == 0 && symlink(target, "link") == 0 && chdir(directory) == 0);
    char bytes[128]; memset(bytes, 0x55, sizeof(bytes));
    assert(readlinkat(dfd, "link", bytes, sizeof(bytes)) == (ssize_t)strlen(target));
    assert(memcmp(bytes, target, strlen(target)) == 0 && bytes[strlen(target)] == 0x55);
    memset(bytes, 0x55, sizeof(bytes));
    assert(readlinkat(dfd, "link", bytes, 3) == 3);
    assert(memcmp(bytes, target, 3) == 0 && bytes[3] == 0x55);
    errno = 0; assert(readlinkat(AT_FDCWD, "link", bytes, sizeof(bytes)) == -1 && errno == ENOENT);
    assert(pthread_fchdir_np(dfd) == 0);
    assert(readlinkat(AT_FDCWD, "link", bytes, sizeof(bytes)) == (ssize_t)strlen(target));
    assert(pthread_fchdir_np(-1) == 0);
    errno = 0; assert(readlinkat(dfd, "link", bytes, 0) == -1 && errno == EINVAL);
    errno = 0; assert(readlinkat(dfd, "link", bytes, (size_t)-1) == -1 && errno == EINVAL);
    errno = 0; assert(readlinkat(dfd, "link", NULL, sizeof(bytes)) == -1 && errno == EFAULT);
    errno = 0; assert(readlinkat(dfd, "missing", NULL, sizeof(bytes)) == -1 && errno == ENOENT);
    errno = 0; assert(readlinkat(dfd, NULL, bytes, sizeof(bytes)) == -1 && errno == EFAULT);
    char longpath[4097]; memset(longpath, 'x', sizeof(longpath));
    errno = 0; assert(readlinkat(dfd, longpath, bytes, sizeof(bytes)) == -1 && errno == ENAMETOOLONG);
    longpath[4096] = 0;
    errno = 0; assert(readlinkat(dfd, longpath, bytes, sizeof(bytes)) == -1 && errno == ENAMETOOLONG);
    assert(unlinkat(dfd, "link", 0) == 0 && chdir("/") == 0);
    assert(close(dfd) == 0 && close(cwd) == 0);
    assert(rmdir(dest) == 0 && rmdir(directory) == 0);
    puts("Rust readlinkat regression checks passed");
    return 0;
}
