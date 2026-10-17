// Regression cases intentionally outside the valid-input C/Rust differential.
#include <assert.h>
#include <limits.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <darling/emulation/common/guarded/table.h>
unsigned __int128 cerror(int error) { (void)error; abort(); }
_Noreturn void __simple_abort(void) { abort(); }
#ifdef DEARUST_GUARDED_FORK
extern void guard_table_prefork(void);
extern void guard_table_postfork_parent(void);
static _Atomic int stop_worker;
static _Atomic unsigned long transitions;
static void close_a(int fd) { assert(fd == 10); }
static void close_b(int fd) { assert(fd == 1034); }
static void *mutate(void *unused) {
    (void)unused;
    guard_entry_options_t a = {.close = close_a}, b = {.close = close_b};
    while (!atomic_load(&stop_worker)) {
        assert(guard_table_add(10, guard_flag_close_on_fork, &a) == 0);
        assert(guard_table_remove(10) == 0);
        assert(guard_table_add(1034, guard_flag_close_on_fork, &b) == 0);
        assert(guard_table_remove(1034) == 0);
        atomic_fetch_add(&transitions, 1);
    }
    return NULL;
}
static void fork_concurrent(void) {
    pthread_t thread;
    assert(pthread_create(&thread, NULL, mutate, NULL) == 0);
    while (!atomic_load(&transitions)) {}
    for (int i = 0; i < 100; ++i) {
        guard_table_prefork();
        int pid = fork();
        if (pid != 0) guard_table_postfork_parent();
        assert(pid >= 0);
        if (pid == 0) {
            alarm(5);
            guard_table_postfork_child();
            assert(!guard_table_check(10, guard_flag_close_on_fork));
            assert(!guard_table_check(1034, guard_flag_close_on_fork));
            _exit(0);
        }
        int status;
        assert(waitpid(pid, &status, 0) == pid);
        assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    }
    atomic_store(&stop_worker, 1);
    assert(pthread_join(thread, NULL) == 0);
    assert(atomic_load(&transitions) > 0);
    puts("100 forks preserve callback identity while guard mutations remain active");
}
static void fork_failure(void) {
    struct rlimit saved, limit;
    assert(getrlimit(RLIMIT_NPROC, &saved) == 0);
    limit = saved; limit.rlim_cur = 0;
    assert(setrlimit(RLIMIT_NPROC, &limit) == 0);
    guard_table_prefork();
    int pid = fork(); int error = errno;
    if (pid != 0) guard_table_postfork_parent();
    if (pid == 0) { guard_table_postfork_child(); _exit(0); }
    assert(setrlimit(RLIMIT_NPROC, &saved) == 0);
    assert(pid == -1 && error == EAGAIN);
    assert(guard_table_add(12, guard_flag_prevent_close, NULL) == 0);
    assert(guard_table_remove(12) == 0);
    puts("failed fork releases guard lock");
}
#endif
static int replacement;
static void replace_guard(int fd) {
    // Removing the old guard may report absent: Rust retires before callbacks.
    int removed = guard_table_remove(fd);
    assert(removed == 0 || removed == -2);
    assert(guard_table_add(fd, guard_flag_prevent_close, NULL) == 0);
    replacement++;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    alarm(20);
    #ifdef DEARUST_GUARDED_FORK
    if (strcmp(argv[1], "fork") == 0) { fork_concurrent(); return 0; }
    if (strcmp(argv[1], "fork-failure") == 0) { fork_failure(); return 0; }
    #endif
    if (strcmp(argv[1], "full") == 0) {
        for (int fd = 0; fd < 1024; ++fd) assert(guard_table_add(fd, 1, NULL) == 0);
        int result = guard_table_check(-3, 1);
        assert(result == 0);
        for (int fd = 0; fd < 1024; ++fd) assert(guard_table_remove(fd) == 0);
        for (int fd = -3; fd < 0; ++fd) {
            assert(guard_table_add(fd, 1, NULL) == -9);
            assert(guard_table_modify(fd, 1, NULL) == -9);
            assert(guard_table_remove(fd) == -9);
        }
        assert(guard_table_add(INT_MIN, 1, NULL) == -9);
        puts("negative fds rejected after full-table search");
    } else if (strcmp(argv[1], "sentinel") == 0) {
        int result = guard_table_modify(-1, 1, NULL);
        printf("modify(-1)=%d check(-1)=%d\n", result, guard_table_check(-1, 1));
        return result == -9 && guard_table_check(-1, 1) == 0 ? 0 : 1;
    } else if (strcmp(argv[1], "callback") == 0) {
        guard_entry_options_t options = {.close = replace_guard};
        assert(guard_table_add(5, guard_flag_close_on_fork, &options) == 0);
        guard_table_postfork_child();
        assert(replacement == 1);
        int present = guard_table_check(5, guard_flag_prevent_close);
        printf("callback replacement preserved=%d\n", present);
        return present ? 0 : 1;
    } else { abort(); }
    return 0;
}
