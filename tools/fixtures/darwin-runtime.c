// Host executable test driver. Production guard table and close_internal are
// extracted from pinned sources. Only cerror TLS and panic termination are host
// adapters: Darwin's real TLS/cancellation/server initialization is out of scope.
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/wait.h>
#include <unistd.h>
#include <darling/emulation/common/guarded/table.h>
#include <darling/emulation/xnu_syscall/bsd/impl/unistd/close.h>
extern int __linux_futex(int *, int, int, const struct timespec *, int *, int);
extern int __linux_futex_reterr(int *, int, int, const struct timespec *, int *, int);
static _Thread_local int darwin_errno;
long cerror(int error) { darwin_errno = error; return -1; }
_Noreturn void __simple_abort(void) { abort(); }
static int callback_fd = -1;
static void on_close(int fd) { callback_fd = fd; }
static void *worker(void *argument) {
    intptr_t id = (intptr_t)argument;
    int fd = 20 + (int)id * 1024;
    int word = 0;
    darwin_errno = 200 + (int)id;
    assert(__linux_futex_reterr(&word, 128, 1, NULL, NULL, 0) == -35);
    assert(darwin_errno == 200 + (int)id);
    assert(__linux_futex(&word, 128, 1, NULL, NULL, 0) == -1);
    assert(darwin_errno == 35);
    for (unsigned i = 0; i < 15000; ++i) {
        assert(guard_table_add(fd, guard_flag_prevent_close, NULL) == 0);
        assert(guard_table_check(fd, guard_flag_prevent_close));
        assert(guard_table_modify(fd, guard_flag_none, NULL) == 0);
        assert(!guard_table_check(fd, guard_flag_prevent_close));
        assert(guard_table_remove(fd) == 0);
    }
    return NULL;
}
int main(void) {
    int word = 0;
    struct timespec immediate = {0, 0};
    darwin_errno = 1234;
    assert(__linux_futex_reterr(&word, 128, 1, NULL, NULL, 0) == -35); // Darwin EAGAIN
    assert(darwin_errno == 1234);
    assert(__linux_futex_reterr(&word, 128, 0, &immediate, NULL, 0) == -60); // ETIMEDOUT
    assert(__linux_futex_reterr(NULL, 128, 0, &immediate, NULL, 0) == -14);
    assert(__linux_futex_reterr(&word, 9|128, 0, &immediate, NULL, 0) == -22); // empty bit mask
    assert(__linux_futex(&word, 128, 1, NULL, NULL, 0) == -1);
    assert(darwin_errno == 35);
    assert(__linux_futex(&word, 129, 1, NULL, NULL, 0) == 0);
    assert(darwin_errno == 35);
    assert(guard_table_add(7, guard_flag_prevent_close, NULL) == 0);
    assert(guard_table_add(1031, guard_flag_prevent_close, NULL) == 0);
    assert(guard_table_add(7, guard_flag_none, NULL) == -17);
    assert(guard_table_remove(7) == 0);
    assert(guard_table_check(1031, guard_flag_prevent_close));
    assert(guard_table_remove(1031) == 0);
    assert(guard_table_remove(1031) == -2);
    darwin_errno = 999;
    pthread_t threads[8];
    for (intptr_t i = 0; i < 8; ++i) assert(pthread_create(&threads[i], NULL, worker, (void *)i) == 0);
    for (int i = 0; i < 8; ++i) assert(pthread_join(threads[i], NULL) == 0);
    assert(darwin_errno == 999);
    // Fill all slots, including wraparound probing, and recover all slots.
    for (int i = 0; i < 1024; ++i) assert(guard_table_add(1023+i*1024, guard_flag_none, NULL) == 0);
    assert(guard_table_add(1024*1024+1023, guard_flag_none, NULL) == -12);
    for (int i = 0; i < 1024; ++i) assert(guard_table_remove(1023+i*1024) == 0);
    int descriptors[2];
    assert(pipe(descriptors) == 0);
    guard_entry_options_t custom = {.close = on_close};
    assert(guard_table_add(5000, guard_flag_close_on_fork, &custom) == 0);
    assert(guard_table_add(descriptors[0], guard_flag_close_on_fork, NULL) == 0);
    assert(guard_table_add(descriptors[1], guard_flag_prevent_close, NULL) == 0);
    pid_t child = fork();
    assert(child >= 0);
    if (child == 0) {
        alarm(10);
        guard_table_postfork_child();
        assert(callback_fd == 5000);
        assert(!guard_table_check(5000, guard_flag_close_on_fork));
        assert(!guard_table_check(descriptors[0], guard_flag_close_on_fork));
        assert(fcntl(descriptors[0], F_GETFD) == -1 && errno == EBADF);
        assert(fcntl(descriptors[1], F_GETFD) != -1);
        assert(guard_table_check(descriptors[1], guard_flag_prevent_close));
        _exit(0);
    }
    int status;
    assert(waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    assert(callback_fd == -1);
    assert(guard_table_check(descriptors[0], guard_flag_close_on_fork));
    assert(fcntl(descriptors[0], F_GETFD) != -1);
    assert(guard_table_remove(5000) == 0);
    assert(guard_table_remove(descriptors[0]) == 0);
    assert(close_internal(descriptors[0]) == 0);
    assert(guard_table_remove(descriptors[1]) == 0);
    assert(close_internal(descriptors[1]) == 0);
    assert(close_internal(descriptors[1]) == -9);
    puts("guard_table: collisions, tombstones, capacity, 120000 concurrent cycles, postfork callback/raw close; futex: EAGAIN/ETIMEDOUT/EFAULT/EINVAL and cerror TLS");
    return 0;
}
