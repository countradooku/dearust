/* Actual Darling sparse >4GiB sizing without a corresponding memory allocation. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
int main(void) {
    char name[] = "/tmp/dearust-ftruncate-XXXXXX"; int fd = mkstemp(name); assert(fd >= 0);
    assert(write(fd, "kept", 4) == 4 && lseek(fd, 2, SEEK_SET) == 2);
    off_t large = (INT64_C(1) << 32) + 123;
    assert(ftruncate(fd, large) == 0);
    struct stat st; assert(fstat(fd, &st) == 0 && st.st_size == large && lseek(fd, 0, SEEK_CUR) == 2);
    unsigned char data[8]; memset(data, 0xa5, sizeof(data));
    assert(pread(fd, data + 1, 1, large - 1) == 1 && data[1] == 0 && data[0] == 0xa5 && data[2] == 0xa5);
    assert(pread(fd, data + 1, 4, 0) == 4 && memcmp(data + 1, "kept", 4) == 0 && data[0] == 0xa5 && data[5] == 0xa5);
    assert(ftruncate(fd, 2) == 0 && fstat(fd, &st) == 0 && st.st_size == 2 && lseek(fd, 0, SEEK_CUR) == 2);
    errno = 0; assert(ftruncate(fd, -1) == -1 && errno == EINVAL);
    errno = 0; assert(ftruncate(fd, INT64_MIN) == -1 && errno == EINVAL);
    errno = 0; assert(ftruncate(-42, 0) == -1 && errno == EBADF);
    assert(fstat(fd, &st) == 0 && st.st_size == 2);
    assert(ftruncate(fd, 0) == 0 && fstat(fd, &st) == 0 && st.st_size == 0 && lseek(fd, 0, SEEK_CUR) == 2);
    assert(close(fd) == 0 && unlink(name) == 0);
    puts("Rust ftruncate regression checks passed");
}
