/* Darling GPL-3.0-or-later ABI: explicit raw adapter and real Linux syscall. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
extern long sys_ftruncate(int, long long);
static int expected_fd, calls, injected;
static long long expected_length;
static long raw_result;
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(nr == SYS_ftruncate && a == expected_fd && b == expected_length);
    assert(c == 0 && d == 0 && e == 0 && f == 0); calls++;
    if (injected) return raw_result;
    long result = syscall(nr, a, b);
    return result < 0 ? -errno : result;
}
int main(void) {
    char name[] = "/tmp/dearust-ftruncate-XXXXXX"; int fd = mkstemp(name); assert(fd >= 0);
    assert(write(fd, "kept", 4) == 4 && lseek(fd, 2, SEEK_SET) == 2);
    expected_fd = fd; expected_length = (INT64_C(1) << 32) + 123;
    assert(sys_ftruncate(fd, expected_length) == 0);
    struct stat st; assert(fstat(fd, &st) == 0 && st.st_size == expected_length && lseek(fd, 0, SEEK_CUR) == 2);
    unsigned char data[8]; memset(data, 0xa5, sizeof(data));
    assert(pread(fd, data + 1, 1, expected_length - 1) == 1 && data[1] == 0 && data[0] == 0xa5 && data[2] == 0xa5);
    assert(pread(fd, data + 1, 4, 0) == 4 && memcmp(data + 1, "kept", 4) == 0 && data[0] == 0xa5 && data[5] == 0xa5);
    expected_length = 2; assert(sys_ftruncate(fd, 2) == 0);
    assert(fstat(fd, &st) == 0 && st.st_size == 2 && lseek(fd, 0, SEEK_CUR) == 2);
    expected_length = -1; assert(sys_ftruncate(fd, -1) == -22);
    expected_length = INT64_MIN; assert(sys_ftruncate(fd, expected_length) == -22);
    expected_fd = -42; expected_length = 0; assert(sys_ftruncate(-42, 0) == -9);
    injected = 1; raw_result = INT64_C(0x100000007); assert(sys_ftruncate(-42, 0) == 7);
    raw_result = INT64_C(0x1fffffff5); assert(sys_ftruncate(-42, 0) == -35);
    assert(calls == 7 && close(fd) == 0 && unlink(name) == 0);
    puts("ftruncate: real sparse sizing, offsets, bounds, signed arguments and errno narrowing");
}
