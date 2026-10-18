/* Darling GPL-3.0-or-later ABI: explicit raw adapter and real Linux syscall. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
extern long sys_fchmod(int, int);
static int expected_fd, expected_mode, calls, injected;
static long raw_result;
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(nr == SYS_fchmod && a == expected_fd && b == expected_mode);
    assert(c == 0 && d == 0 && e == 0 && f == 0); calls++;
    if (injected) return raw_result;
    long result = syscall(nr, a, b);
    return result < 0 ? -errno : result;
}
int main(void) {
    char name[] = "/tmp/dearust-fchmod-XXXXXX"; int fd = mkstemp(name); assert(fd >= 0);
    assert(write(fd, "kept", 4) == 4 && lseek(fd, 2, SEEK_SET) == 2);
    expected_fd = fd; expected_mode = 0641;
    assert(sys_fchmod(fd, expected_mode) == 0);
    struct stat st; assert(fstat(fd, &st) == 0 && (st.st_mode & 07777) == 0641 && st.st_size == 4);
    assert(lseek(fd, 0, SEEK_CUR) == 2);
    expected_mode = -1; assert(sys_fchmod(fd, -1) == 0);
    assert(fstat(fd, &st) == 0 && (st.st_mode & 0777) == 0777);
    expected_fd = -42; assert(sys_fchmod(-42, -1) == -9);
    injected = 1; raw_result = INT64_C(0x100000007);
    assert(sys_fchmod(-42, -1) == 7);
    raw_result = INT64_C(0x1fffffff5); /* int -11 Linux EAGAIN -> Darwin -35, once */
    assert(sys_fchmod(-42, -1) == -35);
    assert(calls == 5 && close(fd) == 0 && unlink(name) == 0);
    puts("fchmod: real modes, fd errors, signed arguments, narrowing and one errno conversion");
}
