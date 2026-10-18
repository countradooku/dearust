/* Actual Darling permissions, dirfd, symlink and per-thread cwd regression. */
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
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600); assert(fd >= 0); assert(close(fd) == 0);
}
static unsigned permissions(const char *path) {
    struct stat st; assert(stat(path, &st) == 0); return st.st_mode & 0777;
}
int main(void) {
    char directory[] = "/tmp/dearust-fchmodat-XXXXXX"; assert(mkdtemp(directory));
    char dest[256]; snprintf(dest, sizeof(dest), "%s/dest", directory);
    assert(mkdir(dest, 0700) == 0 && chdir(dest) == 0);
    touch("target"); touch("thread"); assert(symlink("target", "link") == 0);
    int dfd = open(dest, O_RDONLY); assert(dfd >= 0);
    assert(chdir(directory) == 0); touch("target"); touch("thread");
    assert(fchmodat(dfd, "target", 0640, 0) == 0 && permissions("target") == 0600);
    assert(fchmodat(dfd, "link", 0777, AT_SYMLINK_NOFOLLOW) == 0);
    struct stat st; assert(fstatat(dfd, "target", &st, 0) == 0 && (st.st_mode & 0777) == 0640);
    errno = 0; assert(fchmodat(dfd, "missing", 0600, AT_SYMLINK_NOFOLLOW) == -1 && errno == ENOENT);
    errno = 0; assert(fchmodat(dfd, "target", 0600, (int)0x80000000) == -1 && errno == EINVAL);
    errno = 0; assert(fchmodat(dfd, NULL, 0600, 0) == -1 && errno == EFAULT);
    char longpath[4097]; memset(longpath, 'x', sizeof(longpath)); longpath[4096] = 0;
    errno = 0; assert(fchmodat(dfd, longpath, 0600, 0) == -1 && errno == ENAMETOOLONG);
    assert(pthread_fchdir_np(dfd) == 0);
    assert(fchmodat(AT_FDCWD, "thread", 0604, 0) == 0);
    assert(pthread_fchdir_np(-1) == 0 && permissions("thread") == 0600);
    assert(chdir(dest) == 0 && permissions("thread") == 0604 && permissions("target") == 0640);
    assert(unlink("thread") == 0 && unlink("target") == 0 && unlink("link") == 0);
    assert(chdir(directory) == 0 && unlink("thread") == 0 && unlink("target") == 0);
    assert(close(dfd) == 0 && chdir("/") == 0 && rmdir(dest) == 0 && rmdir(directory) == 0);
    puts("Rust fchmodat regression checks passed");
}
