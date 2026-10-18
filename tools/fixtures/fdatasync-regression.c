/* Actual Darling fdatasync syscall and errno regression. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>
/* xnu syscalls.master assigns fdatasync Darwin syscall187. */
static int run_sync(int fd) { return (int)syscall(SYS_fdatasync, fd); }
int main(void) {
    char path[] = "/tmp/dearust-fdatasync-XXXXXX";
    int fd = mkstemp(path); assert(fd >= 0);
    assert(write(fd, "persist", 7) == 7 && run_sync(fd) == 0);
    assert(close(fd) == 0);
    errno = 0; assert(run_sync(fd) == -1 && errno == EBADF);
    errno = 0; assert(run_sync(-1) == -1 && errno == EBADF);
    int pipefd[2]; assert(pipe(pipefd) == 0);
    errno = 0; assert(run_sync(pipefd[1]) == -1 && errno == EINVAL);
    assert(close(pipefd[0]) == 0 && close(pipefd[1]) == 0);
    fd = open(path, O_RDONLY); assert(fd >= 0);
    char data[7]; assert(read(fd, data, sizeof(data)) == sizeof(data));
    assert(data[0] == 'p' && data[6] == 't');
    assert(close(fd) == 0 && unlink(path) == 0);
    puts("Rust fdatasync regression checks passed");
}
