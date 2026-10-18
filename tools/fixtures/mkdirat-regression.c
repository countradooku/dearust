/* Actual Darling dirfd, umask/mode, bounded path and per-thread cwd regression. */
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
    char dir[] = "/tmp/dearust-mkdirat-XXXXXX"; assert(mkdtemp(dir));
    char dest[256]; snprintf(dest, sizeof(dest), "%s/dest", dir);
    assert(mkdir(dest, 0700) == 0);
    int fd = open(dest, O_RDONLY); assert(fd >= 0 && chdir(dir) == 0);
    int kept = open("kept", O_CREAT | O_EXCL | O_WRONLY, 0600); assert(kept >= 0 && close(kept) == 0);
    mode_t mask = umask(0027);
    assert(mkdirat(fd, "made", 0777) == 0);
    struct stat st; assert(fstatat(fd, "made", &st, 0) == 0 && (st.st_mode & 0777) == 0750);
    errno = 0; assert(stat("made", &st) == -1 && errno == ENOENT);
    errno = 0; assert(mkdirat(fd, "made", 0700) == -1 && errno == EEXIST);
    errno = 0; assert(mkdirat(fd, "missing/child", 0700) == -1 && errno == ENOENT);
    assert(symlinkat("made", fd, "link") == 0);
    errno = 0; assert(mkdirat(fd, "link", 0700) == -1 && errno == EEXIST);
    errno = 0; assert(mkdirat(fd, NULL, 0700) == -1 && errno == EFAULT);
    char longpath[4097]; memset(longpath, 'x', sizeof(longpath));
    errno = 0; assert(mkdirat(fd, longpath, 0700) == -1 && errno == ENAMETOOLONG);
    longpath[4096] = 0;
    errno = 0; assert(mkdirat(fd, longpath, 0700) == -1 && errno == ENAMETOOLONG);
    assert(pthread_fchdir_np(fd) == 0 && mkdirat(AT_FDCWD, "thread", 0700) == 0);
    assert(pthread_fchdir_np(-1) == 0);
    errno = 0; assert(stat("thread", &st) == -1 && errno == ENOENT);
    assert(stat("kept", &st) == 0 && S_ISREG(st.st_mode));
    assert(unlinkat(fd, "link", 0) == 0 && unlinkat(fd, "made", AT_REMOVEDIR) == 0);
    assert(unlinkat(fd, "thread", AT_REMOVEDIR) == 0 && close(fd) == 0);
    umask(mask); assert(unlink("kept") == 0 && chdir("/") == 0 && rmdir(dest) == 0 && rmdir(dir) == 0);
    puts("Rust mkdirat regression checks passed");
}
