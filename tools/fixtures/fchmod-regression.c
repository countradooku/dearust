/* Actual Darling descriptor mode changes, content and offset preservation. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
int main(void) {
    char name[] = "/tmp/dearust-fchmod-XXXXXX"; int fd = mkstemp(name); assert(fd >= 0);
    assert(write(fd, "kept", 4) == 4 && lseek(fd, 2, SEEK_SET) == 2);
    assert(fchmod(fd, 0641) == 0);
    struct stat st; assert(fstat(fd, &st) == 0 && (st.st_mode & 07777) == 0641 && st.st_size == 4);
    assert(lseek(fd, 0, SEEK_CUR) == 2);
    unsigned char data[6]; memset(data, 0xa5, sizeof(data));
    assert(pread(fd, data + 1, 4, 0) == 4 && memcmp(data + 1, "kept", 4) == 0 && data[0] == 0xa5 && data[5] == 0xa5);
    assert(lseek(fd, 0, SEEK_CUR) == 2);
    errno = 0; assert(fchmod(-42, 0600) == -1 && errno == EBADF);
    assert(fchmod(fd, 0600) == 0 && fstat(fd, &st) == 0 && (st.st_mode & 07777) == 0600);
    assert(close(fd) == 0 && unlink(name) == 0);
    puts("Rust fchmod regression checks passed");
}
