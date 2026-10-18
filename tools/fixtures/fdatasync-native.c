/* Darling GPL-3.0-or-later ABI; deterministic recording and actual Linux IO. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>
extern long sys_fdatasync(int);
static int recording, expected_fd, calls;
static long recorded_result;
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(nr == 75 && b == 0 && c == 0 && d == 0 && e == 0 && f == 0);
    assert(a == (long)expected_fd); calls++;
    if (recording) return recorded_result;
    long result = syscall(nr, a);
    return result < 0 ? -errno : result;
}
static void check(long (*call)(int)) {
    recording = 1;
    expected_fd = INT_MIN; recorded_result = -9; assert(call(INT_MIN) == -9);
    expected_fd = INT_MAX; recorded_result = -95; assert(call(INT_MAX) == -102);
    expected_fd = -1; recorded_result = -4; assert(call(-1) == -4);
    recorded_result = 0; assert(call(-1) == 0);
    recorded_result = 0x100000007L; assert(call(-1) == 7);
    recorded_result = 0x1fffffff7L; assert(call(-1) == -9);
    recorded_result = -0x100000000L; assert(call(-1) == 0);
    recording = 0;
    char path[] = "/tmp/dearust-fdatasync-XXXXXX";
    int fd = mkstemp(path); assert(fd >= 0);
    assert(write(fd, "persist", 7) == 7);
    expected_fd = fd; assert(call(fd) == 0);
    assert(close(fd) == 0); assert(call(fd) == -9);
    expected_fd = -1; assert(call(-1) == -9);
    int pipefd[2]; assert(pipe(pipefd) == 0);
    expected_fd = pipefd[1]; assert(call(pipefd[1]) == -22);
    assert(close(pipefd[0]) == 0 && close(pipefd[1]) == 0);
    assert(unlink(path) == 0);
}
int main(void) {
    check(sys_fdatasync);
    assert(calls == 11);
    puts("fdatasync: raw syscall ABI, signed fd, C int result, errno, real file and pipe checks passed");
}
