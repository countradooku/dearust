// Test-only glibc allocation ABI interposition. The guard is armed only after
// actual startup and only denies allocations on the dedicated cleanup native
// lane. Direct glibc-internal hidden allocation calls are outside this ABI scope.
extern bool dearust_actual_cleanup_allocation_denied() noexcept;
extern "C" void* __libc_malloc(size_t) noexcept;
extern "C" void* __libc_calloc(size_t, size_t) noexcept;
extern "C" void* __libc_realloc(void*, size_t) noexcept;
extern "C" void* __libc_memalign(size_t, size_t) noexcept;
extern "C" void* __libc_valloc(size_t) noexcept;
extern "C" void* __libc_pvalloc(size_t) noexcept;
extern "C" void* malloc(size_t size) noexcept {
    if (dearust_actual_cleanup_allocation_denied()) { errno = ENOMEM; return nullptr; }
    return __libc_malloc(size);
}
extern "C" void* calloc(size_t count, size_t size) noexcept {
    if (dearust_actual_cleanup_allocation_denied()) { errno = ENOMEM; return nullptr; }
    return __libc_calloc(count, size);
}
extern "C" void* realloc(void* pointer, size_t size) noexcept {
    if (dearust_actual_cleanup_allocation_denied()) { errno = ENOMEM; return nullptr; }
    return __libc_realloc(pointer, size);
}
extern "C" void* memalign(size_t alignment, size_t size) noexcept {
    if (dearust_actual_cleanup_allocation_denied()) { errno = ENOMEM; return nullptr; }
    return __libc_memalign(alignment, size);
}
extern "C" void* aligned_alloc(size_t alignment, size_t size) noexcept {
    if (dearust_actual_cleanup_allocation_denied()) { errno = ENOMEM; return nullptr; }
    return __libc_memalign(alignment, size);
}
extern "C" int posix_memalign(void** output, size_t alignment, size_t size) noexcept {
    if (alignment < sizeof(void*) || (alignment & (alignment - 1))) return EINVAL;
    if (dearust_actual_cleanup_allocation_denied()) return ENOMEM;
    void* result = __libc_memalign(alignment, size);
    if (!result) return ENOMEM;
    *output = result;
    return 0;
}
extern "C" void* valloc(size_t size) noexcept {
    if (dearust_actual_cleanup_allocation_denied()) { errno = ENOMEM; return nullptr; }
    return __libc_valloc(size);
}
extern "C" void* pvalloc(size_t size) noexcept {
    if (dearust_actual_cleanup_allocation_denied()) { errno = ENOMEM; return nullptr; }
    return __libc_pvalloc(size);
}
