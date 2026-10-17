#define _GNU_SOURCE
#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <libsimple/lock.h>

#define THREADS 12
#define GENERATIONS 100
#define ABI(type) _Static_assert(sizeof(type) == 4 && _Alignof(type) == 4, #type " ABI")
ABI(libsimple_lock_t);
ABI(libsimple_once_t);
ABI(libsimple_rwlock_t);
ABI(libsimple_condvar_t);

static libsimple_lock_t mutex = LIBSIMPLE_LOCK_INITIALIZER;
static libsimple_once_t once = LIBSIMPLE_ONCE_INITIALIZER;
static libsimple_rwlock_t rwlock = LIBSIMPLE_RWLOCK_INITIALIZER;
static libsimple_condvar_t cond = LIBSIMPLE_CONDVAR_INITIALIZER;
static libsimple_condvar_t progress = LIBSIMPLE_CONDVAR_INITIALIZER;
static pthread_barrier_t start;
static _Atomic unsigned attempts, callbacks;
static uint64_t published[32], counter, rw_value;
static _Atomic unsigned readers, writer;
static unsigned ready, completed, phase;

static void initialize(void *context) {
    assert(context == published);
    atomic_fetch_add(&callbacks, 1);
    // At least one contender must have marked the once before publication. This
    // deliberately exercises waiting callers and completion/wake-all ordering.
    while (atomic_load(&attempts) != THREADS || __atomic_load_n(&once.state, __ATOMIC_ACQUIRE) != 2)
        sched_yield();
    for (unsigned i = 0; i < 32; ++i) published[i] = UINT64_C(0x1234567800000000) + i;
}
static void *run(void *argument) {
    uintptr_t id = (uintptr_t)argument;
    pthread_barrier_wait(&start);
    atomic_fetch_add(&attempts, 1);
    libsimple_once(&once, initialize, published);
    for (unsigned i = 0; i < 32; ++i) assert(published[i] == UINT64_C(0x1234567800000000) + i);
    for (unsigned i = 0; i < 4000; ++i) {
        libsimple_lock_lock(&mutex);
        ++counter;
        assert(!libsimple_lock_try_lock(&mutex));
        libsimple_lock_unlock(&mutex);
        if (id % 3 == 0) {
            libsimple_rwlock_lock_write(&rwlock);
            assert(atomic_load(&readers) == 0 && atomic_exchange(&writer, 1) == 0);
            ++rw_value;
            assert(!libsimple_rwlock_try_lock_read(&rwlock));
            assert(!libsimple_rwlock_try_lock_write(&rwlock));
            atomic_store(&writer, 0);
            libsimple_rwlock_unlock_write(&rwlock);
        } else {
            libsimple_rwlock_lock_read(&rwlock);
            atomic_fetch_add(&readers, 1);
            assert(atomic_load(&writer) == 0);
            assert(rw_value <= 16000);
            atomic_fetch_sub(&readers, 1);
            libsimple_rwlock_unlock_read(&rwlock);
        }
    }
    libsimple_lock_lock(&mutex);
    for (unsigned generation = 1; generation <= GENERATIONS; ++generation) {
        ++ready;
        libsimple_condvar_notify_all(&progress, &mutex);
        while (phase < generation) libsimple_condvar_wait(&cond, &mutex);
        ++completed;
        libsimple_condvar_notify_all(&progress, &mutex);
    }
    libsimple_lock_unlock(&mutex);
    return NULL;
}
int main(void) {
    // Bound deadlocks in CI even if the external test runner has no timeout.
    alarm(30);
    // The no_std C archive must terminate an invalid initializer without a
    // Rust panic runtime, allocation or unwinding across the foreign boundary.
    pid_t child = fork();
    assert(child >= 0);
    if (child == 0) {
        alarm(5);
        struct rlimit limit = {0, 0};
        if (setrlimit(RLIMIT_CORE, &limit) != 0) _exit(90);
        libsimple_once_t invalid = LIBSIMPLE_ONCE_INITIALIZER;
        libsimple_once(&invalid, NULL, NULL);
        _exit(91);
    }
    int status;
    assert(waitpid(child, &status, 0) == child);
    assert(WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT);
    pthread_t threads[THREADS];
    assert(pthread_barrier_init(&start, NULL, THREADS + 1) == 0);
    for (uintptr_t i = 0; i < THREADS; ++i) assert(pthread_create(&threads[i], NULL, run, (void *)i) == 0);
    pthread_barrier_wait(&start);
    libsimple_lock_lock(&mutex);
    for (unsigned generation = 1; generation <= GENERATIONS; ++generation) {
        while (ready < generation * THREADS) libsimple_condvar_wait(&progress, &mutex);
        phase = generation;
        // Multiple sleepers are requeued to the same mutex; every waiter must
        // reacquire in contended state and keep the wake chain alive.
        libsimple_condvar_notify_all(&cond, &mutex);
        while (completed < generation * THREADS) libsimple_condvar_wait(&progress, &mutex);
    }
    libsimple_lock_unlock(&mutex);
    for (unsigned i = 0; i < THREADS; ++i) assert(pthread_join(threads[i], NULL) == 0);
    libsimple_once(&once, NULL, NULL); // completed once never dereferences callback
    assert(atomic_load(&callbacks) == 1);
    assert(counter == THREADS * 4000);
    assert(rw_value == 4 * 4000);
    assert(completed == THREADS * GENERATIONS);
    assert(pthread_barrier_destroy(&start) == 0);
    puts("staged libsimple C ABI stress passed");
    return 0;
}
