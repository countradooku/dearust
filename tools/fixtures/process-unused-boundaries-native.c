/* Native test-only adapters: unused process resource callbacks must fail loudly.
 * Resource fixtures provide their own strong, fault-contained copy boundaries. */
#include <stddef.h>
#include <stdlib.h>
__attribute__((weak)) long dearust_copyin(void *local, const void *guest, size_t length) {
    (void)local; (void)guest; (void)length; abort();
}
__attribute__((weak)) long dearust_copyout(const void *local, void *guest, size_t length) {
    (void)local; (void)guest; (void)length; abort();
}
