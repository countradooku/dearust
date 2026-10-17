// SPDX-License-Identifier: GPL-3.0-or-later
// Native ABI/concurrency harness. Identical source links to pinned C and Rust.
#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <libsimple/lock.h>
extern uint32_t cmpxchg_wrapper_u32(_Atomic uint32_t*,uint32_t,uint32_t);
#define THREADS 8
#define LOOPS 10000
#define ROUNDS 80
#define ABI(type) _Static_assert(sizeof(type)==4 && _Alignof(type)==4,#type " must be 4-byte ABI")
ABI(libsimple_lock_t);
ABI(libsimple_once_t);
ABI(libsimple_rwlock_t);
ABI(libsimple_condvar_t);
static libsimple_lock_t mutex=LIBSIMPLE_LOCK_INITIALIZER;
static libsimple_once_t once=LIBSIMPLE_ONCE_INITIALIZER;
static libsimple_rwlock_t rw=LIBSIMPLE_RWLOCK_INITIALIZER;
static libsimple_condvar_t work=LIBSIMPLE_CONDVAR_INITIALIZER;
static libsimple_condvar_t progress=LIBSIMPLE_CONDVAR_INITIALIZER;
static pthread_barrier_t start;
static uint64_t counter, rw_count, rw_check=0x12345678, published;
static atomic_uint calls, readers, writers;
static _Atomic uint32_t cas_count;
static unsigned generation, ready, completed;
// A real signal must interrupt a kernel futex wait, rather than only simulating
// a spurious return in the safe-core tests. Linux procfs lets the controller
// establish both the original wait and its re-entry without timing guesses.
_Static_assert(ATOMIC_INT_LOCK_FREE==2,"signal acknowledgement must be lock-free");
static atomic_uint signal_calls, interrupted_acquired;
static atomic_int interrupted_tid;
static void acknowledge_signal(int signo) {
    (void)signo;
    atomic_fetch_add_explicit(&signal_calls,1,memory_order_relaxed);
}
static void wait_for_futex(pid_t tid) {
    char path[128];
    assert(snprintf(path,sizeof(path),"/proc/self/task/%d/syscall",tid)>0);
    for(;;) {
        FILE* input=fopen(path,"r");
        assert(input);
        long number=-1;
        int parsed=fscanf(input,"%ld",&number);
        assert(fclose(input)==0);
        if(parsed==1 && number==SYS_futex) return;
        sched_yield();
    }
}
static void* interrupted_mutex_wait(void* ignored) {
    (void)ignored;
    sigset_t signals;
    assert(sigemptyset(&signals)==0);
    assert(sigaddset(&signals,SIGUSR1)==0);
    assert(pthread_sigmask(SIG_UNBLOCK,&signals,NULL)==0);
    atomic_store(&interrupted_tid,(int)syscall(SYS_gettid));
    libsimple_lock_lock(&mutex);
    atomic_store(&interrupted_acquired,1);
    libsimple_lock_unlock(&mutex);
    return NULL;
}
static void test_interrupted_mutex(void) {
    struct sigaction action={.sa_handler=acknowledge_signal}, previous;
    assert(sigemptyset(&action.sa_mask)==0);
    // In particular do not set SA_RESTART: the wait must return EINTR.
    assert(sigaction(SIGUSR1,&action,&previous)==0);
    libsimple_lock_lock(&mutex);
    pthread_t waiter;
    assert(pthread_create(&waiter,NULL,interrupted_mutex_wait,NULL)==0);
    pid_t tid;
    while((tid=atomic_load(&interrupted_tid))==0) sched_yield();
    wait_for_futex(tid);
    assert(pthread_kill(waiter,SIGUSR1)==0);
    while(atomic_load(&signal_calls)==0) sched_yield();
    wait_for_futex(tid);
    assert(atomic_load(&interrupted_acquired)==0);
    libsimple_lock_unlock(&mutex);
    assert(pthread_join(waiter,NULL)==0);
    assert(atomic_load(&interrupted_acquired)==1);
    assert(sigaction(SIGUSR1,&previous,NULL)==0);
}
static void spawn_join(void* (*entry)(void*)) {
    pthread_t threads[THREADS];
    for(uintptr_t i=0;i<THREADS;i++) assert(pthread_create(&threads[i],NULL,entry,(void*)i)==0);
    for(unsigned i=0;i<THREADS;i++) assert(pthread_join(threads[i],NULL)==0);
}
static void initialize(void* context) {
    assert(context==&published);
    assert(atomic_fetch_add(&calls,1)==0);
    struct timespec delay={0,2000000};
    nanosleep(&delay,NULL);
    published=0xfeedface;
}
static void* test_mutex_once(void* ignored) {
    (void)ignored;
    int result=pthread_barrier_wait(&start);
    assert(result==0 || result==PTHREAD_BARRIER_SERIAL_THREAD);
    libsimple_once(&once,initialize,&published);
    assert(published==0xfeedface);
    for(unsigned i=0;i<LOOPS;i++) {
        uint32_t expected=atomic_load_explicit(&cas_count,memory_order_relaxed), observed;
        while((observed=cmpxchg_wrapper_u32(&cas_count,expected,expected+1))!=expected) expected=observed;
        libsimple_lock_lock(&mutex);
        counter++;
        libsimple_lock_unlock(&mutex);
    }
    return NULL;
}
static void* test_rw(void* context) {
    int writer=((uintptr_t)context%2)==0;
    for(unsigned i=0;i<LOOPS;i++) {
        if(writer) {
            libsimple_rwlock_lock_write(&rw);
            assert(atomic_fetch_add(&writers,1)==0);
            assert(atomic_load(&readers)==0);
            rw_count++;
            rw_check=rw_count^0x12345678;
            assert(atomic_fetch_sub(&writers,1)==1);
            libsimple_rwlock_unlock_write(&rw);
        } else {
            libsimple_rwlock_lock_read(&rw);
            atomic_fetch_add(&readers,1);
            assert(atomic_load(&writers)==0);
            assert(rw_check==(rw_count^0x12345678));
            atomic_fetch_sub(&readers,1);
            libsimple_rwlock_unlock_read(&rw);
        }
    }
    return NULL;
}
static void* test_cond(void* ignored) {
    (void)ignored;
    libsimple_lock_lock(&mutex);
    for(unsigned round=1;round<=ROUNDS;round++) {
        ready++;
        libsimple_condvar_notify_one(&progress,&mutex);
        while(generation<round) libsimple_condvar_wait(&work,&mutex);
        completed++;
        libsimple_condvar_notify_one(&progress,&mutex);
    }
    libsimple_lock_unlock(&mutex);
    return NULL;
}
int main(void) {
    alarm(20); // Fail boundedly if a lost wakeup or deadlock occurs.
    _Atomic uint32_t raw=7;
    assert(cmpxchg_wrapper_u32(&raw,6,9)==7 && atomic_load(&raw)==7);
    assert(cmpxchg_wrapper_u32(&raw,7,9)==7 && atomic_load(&raw)==9);
    assert(cmpxchg_wrapper_u32(&raw,9,9)==9 && atomic_load(&raw)==9);
    libsimple_lock_init(&mutex);
    libsimple_once_init(&once);
    libsimple_rwlock_init(&rw);
    libsimple_condvar_init(&work);
    libsimple_condvar_init(&progress);
    assert(libsimple_lock_try_lock(&mutex));
    assert(!libsimple_lock_try_lock(&mutex));
    libsimple_lock_unlock(&mutex);
    assert(pthread_barrier_init(&start,NULL,THREADS)==0);
    spawn_join(test_mutex_once);
    assert(pthread_barrier_destroy(&start)==0);
    assert(counter==THREADS*LOOPS && atomic_load(&calls)==1);
    assert(atomic_load(&cas_count)==THREADS*LOOPS);
    libsimple_once(&once,initialize,&published);
    assert(atomic_load(&calls)==1);
    libsimple_once(&once,NULL,NULL); // Completed once must not inspect callback/context.
    assert(libsimple_rwlock_try_lock_read(&rw));
    assert(libsimple_rwlock_try_lock_read(&rw));
    assert(!libsimple_rwlock_try_lock_write(&rw));
    libsimple_rwlock_unlock_read(&rw);
    libsimple_rwlock_unlock_read(&rw);
    assert(libsimple_rwlock_try_lock_write(&rw));
    assert(!libsimple_rwlock_try_lock_read(&rw));
    assert(!libsimple_rwlock_try_lock_write(&rw));
    libsimple_rwlock_unlock_write(&rw);
    spawn_join(test_rw);
    assert(rw_count==(THREADS/2)*LOOPS);
    pthread_t waiters[THREADS];
    for(unsigned i=0;i<THREADS;i++) assert(pthread_create(&waiters[i],NULL,test_cond,NULL)==0);
    for(unsigned round=1;round<=ROUNDS;round++) {
        libsimple_lock_lock(&mutex);
        while(ready<THREADS*round) libsimple_condvar_wait(&progress,&mutex);
        generation=round;
        libsimple_condvar_notify_all(&work,&mutex);
        while(completed<THREADS*round) libsimple_condvar_wait(&progress,&mutex);
        libsimple_lock_unlock(&mutex);
    }
    for(unsigned i=0;i<THREADS;i++) assert(pthread_join(waiters[i],NULL)==0);
    assert(completed==THREADS*ROUNDS);
    test_interrupted_mutex();
    printf("abi=4:4 mutex=%lu cas=%u once=%u publication=%lu rw=%lu cond=%u eintr=%u\n",
        (unsigned long)counter,atomic_load(&cas_count),atomic_load(&calls),(unsigned long)published,(unsigned long)rw_count,completed,atomic_load(&signal_calls));
    return 0;
}
