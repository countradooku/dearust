/* Real Darwin ABI regression coverage for Dearust's lstat/lstat64 slice. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int main(void) {
    char directory[] = "/tmp/dearust-lstat-XXXXXX";
    assert(mkdtemp(directory) != NULL);
    assert(chdir(directory) == 0);
    int fd = open("regular", O_CREAT | O_EXCL | O_WRONLY, 0600);
    assert(fd >= 0 && write(fd, "hello", 5) == 5 && close(fd) == 0);
    assert(symlink("regular", "link") == 0);
    struct stat st;
    struct stat64 st64;
    assert(lstat("regular", &st) == 0 && S_ISREG(st.st_mode) && st.st_size == 5);
    assert(lstat64("regular", &st64) == 0 && S_ISREG(st64.st_mode) && st64.st_size == 5);
    assert(lstat("link", &st) == 0 && S_ISLNK(st.st_mode) && st.st_size == 7);
    assert(lstat64("link", &st64) == 0 && S_ISLNK(st64.st_mode) && st64.st_size == 7);
    errno = 0; assert(lstat("missing", &st) == -1 && errno == ENOENT);
    errno = 0; assert(lstat64("missing", &st64) == -1 && errno == ENOENT);
    errno = 0; assert(lstat(NULL, &st) == -1 && errno == EFAULT);
    errno = 0; assert(lstat64(NULL, &st64) == -1 && errno == EFAULT);
    errno = 0; assert(lstat("regular", NULL) == -1 && errno == EFAULT);
    errno = 0; assert(lstat64("regular", NULL) == -1 && errno == EFAULT);
    char path[4097]; memset(path, 'x', sizeof(path));
    errno = 0; assert(lstat(path, &st) == -1 && errno == ENAMETOOLONG);
    errno = 0; assert(lstat64(path, &st64) == -1 && errno == ENAMETOOLONG);
    path[4096] = 0;
    errno = 0; assert(lstat(path, &st) == -1 && errno == ENAMETOOLONG);
    errno = 0; assert(lstat64(path, &st64) == -1 && errno == ENAMETOOLONG);
    assert(unlink("link") == 0 && unlink("regular") == 0);
    assert(chdir("/") == 0 && rmdir(directory) == 0);
    puts("Rust lstat regression checks passed");
    return 0;
}
