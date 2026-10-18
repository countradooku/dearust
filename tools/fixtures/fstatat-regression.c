/* Actual Darwin ABI, virtual-root and per-thread-cwd regression coverage. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
/* Exported legacy entry is present even though current SDK omits its prototype. */
extern int fstatat64(int, const char *, struct stat64 *, int);
extern int pthread_fchdir_np(int);
struct narrow { struct stat st; unsigned char tail[32]; };
struct wide { struct stat64 st; unsigned char tail[32]; };
int main(void) {
    char directory[] = "/tmp/dearust-fstatat-XXXXXX";
    assert(mkdtemp(directory));
    int dfd = open(directory, O_RDONLY); assert(dfd >= 0);
    int fd = openat(dfd, "file", O_CREAT | O_EXCL | O_WRONLY, 0600);
    assert(fd >= 0 && write(fd, "payload", 7) == 7 && close(fd) == 0);
    /* Existing sys_symlinkat ignores its dirfd. Keep this fstatat regression
       independent of that unrelated legacy bug by creating an absolute link. */
    char linkpath[256];
    assert(snprintf(linkpath, sizeof(linkpath), "%s/link", directory) > 0);
    assert(symlink("file", linkpath) == 0);
    struct narrow n, nb;
    struct wide w, wb;
    memset(&n, 0xa5, sizeof(n)); memset(&w, 0xa5, sizeof(w));
    assert(fstatat(dfd, "link", &n.st, 0) == 0 && S_ISREG(n.st.st_mode) && n.st.st_size == 7);
    assert(fstatat64(dfd, "link", &w.st, 0) == 0 && S_ISREG(w.st.st_mode) && w.st.st_size == 7);
    assert(fstatat(dfd, "link", &n.st, AT_SYMLINK_NOFOLLOW) == 0 && S_ISLNK(n.st.st_mode));
    assert(fstatat64(dfd, "link", &w.st, AT_SYMLINK_NOFOLLOW) == 0 && S_ISLNK(w.st.st_mode));
    assert(n.st.st_size == 4 && w.st.st_size == 4);
    for (size_t i = 0; i < sizeof(n.tail); i++) assert(n.tail[i] == 0xa5 && w.tail[i] == 0xa5);
    nb = n; wb = w;
#define ERROR_CASE(dir, path, flags, error) do { \
    errno = 0; assert(fstatat(dir, path, &n.st, flags) == -1 && errno == error); \
    errno = 0; assert(fstatat64(dir, path, &w.st, flags) == -1 && errno == error); \
    assert(memcmp(&n, &nb, sizeof(n)) == 0 && memcmp(&w, &wb, sizeof(w)) == 0); \
} while (0)
    ERROR_CASE(dfd, "missing", 0, ENOENT);
    ERROR_CASE(dfd, "file", 0x8000, EINVAL);
    ERROR_CASE(dfd, NULL, 0, EFAULT);
    /* Legacy vchroot resolves dirfds through /proc/self/fd; a nonexistent
       descriptor therefore reports ENOENT before newfstatat sees it. */
    ERROR_CASE(-42, "file", 0, ENOENT);
    char longpath[4097]; memset(longpath, 'x', sizeof(longpath));
    ERROR_CASE(dfd, longpath, 0, ENAMETOOLONG);
    longpath[4096] = 0;
    ERROR_CASE(dfd, longpath, 0, ENAMETOOLONG);
    errno = 0; assert(fstatat(dfd, "file", NULL, 0) == -1 && errno == EFAULT);
    errno = 0; assert(fstatat64(dfd, "file", NULL, 0) == -1 && errno == EFAULT);
    assert(fstatat(AT_FDCWD, directory, &n.st, 0) == 0 && S_ISDIR(n.st.st_mode));
    assert(fstatat64(AT_FDCWD, directory, &w.st, 0) == 0 && S_ISDIR(w.st.st_mode));
    assert(pthread_fchdir_np(dfd) == 0);
    assert(fstatat(AT_FDCWD, "file", &n.st, 0) == 0 && n.st.st_size == 7);
    assert(fstatat64(AT_FDCWD, "file", &w.st, 0) == 0 && w.st.st_size == 7);
    assert(pthread_fchdir_np(-1) == 0);
    assert(unlink(linkpath) == 0 && unlinkat(dfd, "file", 0) == 0);
    assert(close(dfd) == 0 && rmdir(directory) == 0);
    puts("Rust fstatat regression checks passed");
    return 0;
}
