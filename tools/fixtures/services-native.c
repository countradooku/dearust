/* Darling GPL-3.0-or-later process services: recorded ABI and real Linux kernel. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

extern long sys_getentropy(void *, size_t);
extern long sys_thread_selfid(void);

static int injected;
static _Atomic unsigned long calls;
static int expected_nr;
static long expected_address, expected_size;
static long results[16];
static size_t result_count, result_index;

/* The only runtime import supplied by this fixture. The other identifier
 * exports remain linked, but their syscall paths are deliberately unexercised. */
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    atomic_fetch_add_explicit(&calls, 1, memory_order_relaxed);
    if (injected) {
        assert(nr == expected_nr && a == expected_address && b == expected_size);
        assert(c == 0 && d == 0 && e == 0 && f == 0);
        assert(result_index < result_count);
        return results[result_index++];
    }
    long result = syscall(nr, a, b, c, d, e, f);
    return result < 0 ? -errno : result;
}

static void inject(int nr, const void *address, size_t size,
                   const long *values, size_t count) {
    assert(count <= sizeof(results) / sizeof(results[0]));
    injected = 1;
    expected_nr = nr;
    expected_address = (long)(uintptr_t)address;
    expected_size = (long)size;
    memcpy(results, values, count * sizeof(values[0]));
    result_count = count;
    result_index = 0;
}

static void entropy_result(void *address, size_t size, long raw, long expected) {
    inject(SYS_getrandom, address, size, &raw, 1);
    unsigned long before = atomic_load_explicit(&calls, memory_order_relaxed);
    assert(sys_getentropy(address, size) == expected);
    assert(result_index == 1);
    assert(atomic_load_explicit(&calls, memory_order_relaxed) == before + 1);
}

static void test_injected_entropy(void) {
    unsigned char guarded[258];
    memset(guarded, 0x6d, sizeof(guarded));
    unsigned long before = atomic_load_explicit(&calls, memory_order_relaxed);
    injected = 1;
    result_count = result_index = 0;
    assert(sys_getentropy(NULL, 0) == 0);
    assert(sys_getentropy((void *)UINTPTR_MAX, 0) == 0);
    assert(sys_getentropy(NULL, 257) == -EINVAL);
    assert(sys_getentropy((void *)UINTPTR_MAX, SIZE_MAX) == -EINVAL);
    assert(sys_getentropy(guarded + 1, 257) == -EINVAL);
    assert(atomic_load_explicit(&calls, memory_order_relaxed) == before);
    for (size_t i = 0; i < sizeof(guarded); ++i) assert(guarded[i] == 0x6d);

    const size_t sizes[] = {1, 32, 256};
    for (size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); ++i) {
        size_t size = sizes[i];
        entropy_result(guarded + 1, size, (long)size, 0);
        entropy_result(guarded + 1, size, -EFAULT, -14);
        entropy_result(guarded + 1, size, -EAGAIN, -35);
        entropy_result(guarded + 1, size, -ENOSYS, -78);
        entropy_result(guarded + 1, size, -EINVAL, -22);
        entropy_result(guarded + 1, size, -EIO, -5);
        entropy_result(guarded + 1, size, 0, -14);
        if (size > 1) entropy_result(guarded + 1, size, (long)size - 1, -14);
        entropy_result(guarded + 1, size, (long)size + 1, -5);
        const long interrupted[] = {-EINTR, -EINTR, -EINTR, (long)size};
        inject(SYS_getrandom, guarded + 1, size, interrupted, 4);
        before = atomic_load_explicit(&calls, memory_order_relaxed);
        assert(sys_getentropy(guarded + 1, size) == 0 && result_index == 4);
        assert(atomic_load_explicit(&calls, memory_order_relaxed) == before + 4);
        const long interrupted_fault[] = {-EINTR, -EFAULT};
        inject(SYS_getrandom, guarded + 1, size, interrupted_fault, 2);
        assert(sys_getentropy(guarded + 1, size) == -14 && result_index == 2);
        const long interrupted_short[] = {-EINTR, 0};
        inject(SYS_getrandom, guarded + 1, size, interrupted_short, 2);
        assert(sys_getentropy(guarded + 1, size) == -14 && result_index == 2);
    }
    /* No local random scratch is copied out: our synthetic kernel writes none. */
    for (size_t i = 0; i < sizeof(guarded); ++i) assert(guarded[i] == 0x6d);
    injected = 0;
}

static void test_real_entropy(void) {
    unsigned char guarded[258];
    memset(guarded, 0x6d, sizeof(guarded));
    const size_t sizes[] = {0, 1, 256};
    for (size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); ++i) {
        memset(guarded, 0x6d, sizeof(guarded));
        assert(sys_getentropy(guarded + 1, sizes[i]) == 0);
        assert(guarded[0] == 0x6d);
        for (size_t n = sizes[i] + 1; n < sizeof(guarded); ++n)
            assert(guarded[n] == 0x6d);
    }
    unsigned long before = atomic_load_explicit(&calls, memory_order_relaxed);
    assert(sys_getentropy(NULL, 0) == 0);
    assert(sys_getentropy(NULL, 257) == -22);
    assert(sys_getentropy(NULL, SIZE_MAX) == -22);
    assert(atomic_load_explicit(&calls, memory_order_relaxed) == before);
    assert(sys_getentropy(NULL, 1) == -14);
    assert(sys_getentropy(NULL, 256) == -14);

    long page_result = sysconf(_SC_PAGESIZE);
    assert(page_result >= 256);
    size_t page = (size_t)page_result;
    unsigned char *mapping = mmap(NULL, page * 2, PROT_READ | PROT_WRITE,
                                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(mapping != MAP_FAILED);
    memset(mapping, 0x6d, page * 2);
    assert(mprotect(mapping + page, page, PROT_NONE) == 0);
    assert(sys_getentropy(mapping + page, 1) == -14);
    assert(sys_getentropy(mapping + page, 256) == -14);
    for (size_t size = 32; size <= 256; size *= 8) {
        /* Small Linux getrandom copies can report a positive partial write
         * when copy_to_user meets the protected page. Never retry that prefix. */
        errno = 0;
        assert(syscall(SYS_getrandom, mapping + page - 16, size, 0) == 16);
        memset(mapping + page - 17, 0x6d, 17);
        before = atomic_load_explicit(&calls, memory_order_relaxed);
        assert(sys_getentropy(mapping + page - 16, size) == -14);
        assert(atomic_load_explicit(&calls, memory_order_relaxed) == before + 1);
        assert(mapping[page - 17] == 0x6d);
    }
    assert(mprotect(mapping, page, PROT_READ) == 0);
    assert(sys_getentropy(mapping, 1) == -14);
    assert(sys_getentropy(mapping, 256) == -14);
    assert(munmap(mapping, page * 2) == 0);
}

static void test_injected_thread_id(void) {
    const long raw[] = {7, INT64_C(0x100000007), INT64_C(0x1fffffff5),
                        -EAGAIN, -EINTR, -ENOSYS, 0,
                        INT64_C(0x100000000), -INT64_C(0x100000000), INT_MAX};
    const long expected[] = {7, 7, -35, -35, -4, -78, 0, 0, 0, INT_MAX};
    for (size_t i = 0; i < sizeof(raw) / sizeof(raw[0]); ++i) {
        inject(SYS_gettid, NULL, 0, raw + i, 1);
        unsigned long before = atomic_load_explicit(&calls, memory_order_relaxed);
        assert(sys_thread_selfid() == expected[i] && result_index == 1);
        assert(atomic_load_explicit(&calls, memory_order_relaxed) == before + 1);
    }
    injected = 0;
}

enum { THREADS = 8 };
static pthread_barrier_t thread_barrier;
struct thread_result { long id; };
static void *thread_identity(void *opaque) {
    struct thread_result *result = opaque;
    result->id = syscall(SYS_gettid);
    int status = pthread_barrier_wait(&thread_barrier);
    assert(status == 0 || status == PTHREAD_BARRIER_SERIAL_THREAD);
    for (int i = 0; i < 1024; ++i) assert(sys_thread_selfid() == result->id);
    return NULL;
}

static void test_real_thread_id(void) {
    long parent_id = syscall(SYS_gettid);
    assert(sys_thread_selfid() == parent_id && sys_thread_selfid() == parent_id);
    pthread_t threads[THREADS];
    struct thread_result results_out[THREADS];
    assert(pthread_barrier_init(&thread_barrier, NULL, THREADS) == 0);
    for (int i = 0; i < THREADS; ++i)
        assert(pthread_create(threads + i, NULL, thread_identity, results_out + i) == 0);
    for (int i = 0; i < THREADS; ++i) assert(pthread_join(threads[i], NULL) == 0);
    assert(pthread_barrier_destroy(&thread_barrier) == 0);
    for (int i = 0; i < THREADS; ++i) {
        assert(results_out[i].id != parent_id);
        for (int j = 0; j < i; ++j) assert(results_out[i].id != results_out[j].id);
    }
    pid_t child = fork();
    assert(child >= 0);
    if (child == 0) {
        long id = syscall(SYS_gettid);
        assert(id != parent_id && id == getpid());
        assert(sys_thread_selfid() == id && sys_thread_selfid() == id);
        _exit(0);
    }
    int status;
    assert(waitpid(child, &status, 0) == child);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    assert(sys_thread_selfid() == parent_id);
}

int main(void) {
    test_injected_entropy();
    test_real_entropy();
    test_injected_thread_id();
    test_real_thread_id();
    puts("services: entropy limits/errors/retry/faults and uncached per-thread IDs pass");
}
