/* Exercise the production MIG/Mach port ABI, not native Rust test adapters. */
#include <mach/mach.h>
#include <mach/lock_set.h>
#include <mach/sync_policy.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#include <string.h>
#include <signal.h>
#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "line %d: %s\n", __LINE__, #expr); exit(1); } } while (0)
static lock_set_t locks;
static volatile int ready;
static void *wrong_owner(void *unused) {
    (void)unused;
    CHECK(lock_try(locks, 0) == KERN_LOCK_OWNED);
    CHECK(lock_release(locks, 0) == KERN_INVALID_RIGHT);
    CHECK(lock_make_stable(locks, 0) == KERN_INVALID_RIGHT);
    return NULL;
}
static void *dying_owner(void *unused) {
    (void)unused;
    CHECK(lock_acquire(locks, 0) == KERN_SUCCESS);
    return NULL;
}
static void *blocked(void *unused) {
    (void)unused;
    __sync_lock_test_and_set(&ready, 1);
    CHECK(lock_acquire(locks, 0) == KERN_LOCK_SET_DESTROYED);
    return NULL;
}
int main(void) {
    lock_set_t bad;
    CHECK(lock_set_create(mach_task_self(), &bad, 0, SYNC_POLICY_FIFO) == KERN_INVALID_ARGUMENT);
    CHECK(lock_set_create(mach_task_self(), &bad, 1, 7) == KERN_INVALID_ARGUMENT);
    CHECK(lock_set_create(mach_task_self(), &locks, 2, SYNC_POLICY_FIFO) == KERN_SUCCESS);
    CHECK(lock_try(locks, -1) == KERN_INVALID_ARGUMENT);
    CHECK(lock_try(locks, 2) == KERN_INVALID_ARGUMENT);
    CHECK(lock_try(locks, 0) == KERN_SUCCESS);
    CHECK(lock_try(locks, 0) == KERN_LOCK_OWNED_SELF);
    pthread_t t;
    CHECK(pthread_create(&t, NULL, wrong_owner, NULL) == 0);
    CHECK(pthread_join(t, NULL) == 0);
    /* Registered Mach ports are inherited as real send rights, not integer handles. */
    mach_port_array_t saved;
    mach_msg_type_number_t saved_count;
    CHECK(mach_ports_lookup(mach_task_self(), &saved, &saved_count) == KERN_SUCCESS);
    CHECK(mach_ports_register(mach_task_self(), &locks, 1) == KERN_SUCCESS);
    pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        mach_port_array_t inherited;
        mach_msg_type_number_t count;
        CHECK(mach_ports_lookup(mach_task_self(), &inherited, &count) == KERN_SUCCESS);
        CHECK(count >= 1);
        CHECK(lock_try(inherited[0], 0) == KERN_LOCK_OWNED);
        CHECK(lock_release(inherited[0], 0) == KERN_INVALID_RIGHT);
        CHECK(lock_set_destroy(mach_task_self(), inherited[0]) == KERN_INVALID_RIGHT);
        CHECK(lock_try(inherited[0], 1) == KERN_SUCCESS);
        CHECK(lock_release(inherited[0], 1) == KERN_SUCCESS);
        CHECK(mach_port_deallocate(mach_task_self(), inherited[0]) == KERN_SUCCESS);
        _exit(0);
    }
    int status;
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    CHECK(mach_ports_register(mach_task_self(), saved, saved_count) == KERN_SUCCESS);
    for (mach_msg_type_number_t i = 0; i < saved_count; ++i)
        if (MACH_PORT_VALID(saved[i])) mach_port_deallocate(mach_task_self(), saved[i]);
    vm_deallocate(mach_task_self(), (vm_address_t)saved, saved_count * sizeof(*saved));
    CHECK(lock_release(locks, 0) == KERN_SUCCESS);
    CHECK(pthread_create(&t, NULL, dying_owner, NULL) == 0);
    CHECK(pthread_join(t, NULL) == 0);
    kern_return_t kr = KERN_LOCK_OWNED;
    for (int i = 0; i < 5000 && kr == KERN_LOCK_OWNED; ++i) {
        kr = lock_try(locks, 0);
        if (kr == KERN_LOCK_OWNED) usleep(1000);
    }
    CHECK(kr == KERN_LOCK_UNSTABLE);
    CHECK(lock_make_stable(locks, 0) == KERN_SUCCESS);
    CHECK(lock_release(locks, 0) == KERN_SUCCESS);
    CHECK(lock_acquire(locks, 0) == KERN_SUCCESS);
    CHECK(pthread_create(&t, NULL, blocked, NULL) == 0);
    while (!__sync_fetch_and_add(&ready, 0)) usleep(1000);
    usleep(100000);
    CHECK(lock_set_destroy(mach_task_self(), locks) == KERN_SUCCESS);
    CHECK(pthread_join(t, NULL) == 0);
    CHECK(lock_try(locks, 0) == KERN_LOCK_SET_DESTROYED);
    CHECK(mach_port_deallocate(mach_task_self(), locks) == KERN_SUCCESS);
    for (int i = 0; i < 100; ++i) {
        CHECK(lock_set_create(mach_task_self(), &locks, 1, SYNC_POLICY_FIXED_PRIORITY) == KERN_SUCCESS);
        CHECK(lock_set_destroy(mach_task_self(), locks) == KERN_SUCCESS);
        CHECK(mach_port_deallocate(mach_task_self(), locks) == KERN_SUCCESS);
    }
    int ready_pipe[2];
    CHECK(pipe(ready_pipe) == 0);
    child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        close(ready_pipe[0]);
        CHECK(MACH_PORT_VALID(mach_task_self()));
        CHECK(write(ready_pipe[1], "r", 1) == 1);
        for (;;) pause();
    }
    close(ready_pipe[1]);
    char byte;
    CHECK(read(ready_pipe[0], &byte, 1) == 1);
    close(ready_pipe[0]);
    task_t child_task;
    CHECK(task_for_pid(mach_task_self(), child, &child_task) == KERN_SUCCESS);
    CHECK(lock_set_create(child_task, &locks, 1, SYNC_POLICY_FIFO) == KERN_SUCCESS);
    CHECK(kill(child, SIGKILL) == 0);
    CHECK(waitpid(child, &status, 0) == child);
    kr = KERN_SUCCESS;
    for (int i = 0; i < 5000 && kr != KERN_LOCK_SET_DESTROYED; ++i) {
        kr = lock_try(locks, 0);
        if (kr == KERN_SUCCESS) CHECK(lock_release(locks, 0) == KERN_SUCCESS || lock_try(locks, 0) == KERN_LOCK_SET_DESTROYED);
        if (kr != KERN_LOCK_SET_DESTROYED) usleep(1000);
    }
    CHECK(kr == KERN_LOCK_SET_DESTROYED);
    CHECK(mach_port_deallocate(mach_task_self(), locks) == KERN_SUCCESS);
    CHECK(mach_port_deallocate(mach_task_self(), child_task) == KERN_SUCCESS);
    puts("lockset ownership, owner and task death, unstable recovery, blocked destruction, cross-process rights and port lifecycle: PASS");
    return 0;
}
