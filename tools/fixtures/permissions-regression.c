/* Actual Darling TLS-first access/chmod regression, canonical flags-zero paths. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
static void touch(const char *name) {
    int fd = open(name, O_CREAT | O_EXCL | O_WRONLY, 0600); assert(fd >= 0 && close(fd) == 0);
}
static unsigned mode(const char *name) {
    struct stat st; assert(stat(name, &st) == 0); return st.st_mode & 0777;
}
int main(void) {
    char dir[] = "/tmp/dearust-permissions-XXXXXX"; assert(mkdtemp(dir));
    char dest[256]; snprintf(dest, sizeof(dest), "%s/dest", dir);
    assert(mkdir(dest, 0700) == 0 && chdir(dest) == 0); touch("target");
    int fd = open(dest, O_RDONLY); assert(fd >= 0 && chdir(dir) == 0); touch("target");
    assert(pthread_fchdir_np(fd) == 0);
    assert(access("target", R_OK | W_OK) == 0);
    assert(chmod("target", 0640) == 0);
    errno = 0; assert(access("missing", F_OK) == -1 && errno == ENOENT);
    errno = 0; assert(chmod("missing", 0600) == -1 && errno == ENOENT);
    errno = 0; assert(syscall(SYS_access, "target", -1) == -1 && errno == EINVAL);
    errno = 0; assert(syscall(SYS_access, (void *)1, F_OK) == -1 && errno == EFAULT);
    errno = 0; assert(syscall(SYS_chmod, (void *)1, 0600) == -1 && errno == EFAULT);
    errno = 0; assert(syscall(SYS_access, NULL, F_OK) == -1 && errno == EFAULT);
    errno = 0; assert(syscall(SYS_chmod, NULL, 0600) == -1 && errno == EFAULT);
    char longpath[4097]; memset(longpath, 'x', sizeof(longpath)); longpath[4096] = 0;
    errno = 0; assert(access(longpath, F_OK) == -1 && errno == ENAMETOOLONG);
    errno = 0; assert(chmod(longpath, 0600) == -1 && errno == ENAMETOOLONG);
    assert(pthread_fchdir_np(-1) == 0 && mode("target") == 0600);
    assert(chdir(dest) == 0 && mode("target") == 0640);
    assert(unlink("target") == 0 && chdir(dir) == 0 && unlink("target") == 0);
    assert(close(fd) == 0 && chdir("/") == 0 && rmdir(dest) == 0 && rmdir(dir) == 0);
    puts("Rust permissions regression checks passed");
}
