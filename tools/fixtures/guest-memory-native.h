/* Test-only dispatcher for real self-memory operations. Operation fixtures keep
 * their own strict syscall expectations; these calls must never be injected as
 * successful filesystem operations. No production object includes this file. */
#ifndef DEARUST_GUEST_MEMORY_NATIVE_H
#define DEARUST_GUEST_MEMORY_NATIVE_H
#include <errno.h>
#include <unistd.h>
static inline int dearust_native_memory_syscall(long a,long b,long c,long d,long e,long f,int nr,long *result) {
    if(nr!=14 && nr!=39 && nr!=310 && nr!=311) return 0;
    long raw=syscall(nr,a,b,c,d,e,f);
    *result=raw<0?-errno:raw;
    return 1;
}
#endif
