/* Exact original C versus corrected Rust, with a recorded raw kernel boundary.
 * Link original to the verified errno.o converter; no replacement C algorithm. */
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern long sys_getentropy(void *, size_t);
static long expected_address, expected_size, returns[2];
static unsigned calls, available;
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    assert(nr == 318 && a == expected_address && b == expected_size);
    assert(c == 0 && d == 0 && e == 0 && f == 0 && calls < available);
    return returns[calls++];
}

static void check(int fixed, void *pointer, size_t size, long first, long second,
                  long old_result, long new_result, unsigned old_calls,
                  unsigned new_calls) {
    expected_address = (long)(uintptr_t)pointer;
    expected_size = (long)size;
    returns[0] = first;
    returns[1] = second;
    available = 2;
    calls = 0;
    assert(sys_getentropy(pointer, size) == (fixed ? new_result : old_result));
    assert(calls == (fixed ? new_calls : old_calls));
}

int main(int argc, char **argv) {
    assert(argc == 2);
    int fixed = strcmp(argv[1], "--fixed") == 0;
    assert(fixed || strcmp(argv[1], "--original") == 0);
    unsigned char data[258];
    memset(data, 0x6d, sizeof(data));
    /* Original signed ret < unsigned size accidentally reports negative
     * failures as success; the exact original converter is linked unchanged. */
    check(fixed, NULL, 1, -EFAULT, 0, 0, -14, 1, 1);
    check(fixed, data, 256, -EAGAIN, 0, 0, -35, 1, 1);
    check(fixed, data, 256, -ENOSYS, 0, 0, -78, 1, 1);
    check(fixed, data, 257, 257, 0, 0, -22, 1, 0);
    check(fixed, data, 32, 16, 0, -5, -14, 1, 1);
    check(fixed, data, 32, 0, 0, -5, -14, 1, 1);
    check(fixed, data, 32, -EINTR, 32, 0, 0, 1, 2);
    check(fixed, NULL, 0, 0, 0, 0, 0, 1, 0);
    for (size_t i = 0; i < sizeof(data); ++i) assert(data[i] == 0x6d);
    puts(fixed ? "corrected Rust entropy regressions pass" :
                 "exact original C entropy defects reproduced");
}
