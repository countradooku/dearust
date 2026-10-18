/* Process-local transaction epoch changes only after an application handler
 * returns in a newly forked child, which has no surviving sibling threads. */
#ifndef __APPLE__
#define _GNU_SOURCE
#include <ucontext.h>
#include <unistd.h>
#include <sys/syscall.h>
#define linux_ucontext ucontext_t
#define LINUX_SYSCALL(number, value) syscall(number, value)
#define DEARUST_RPC_PC(context) ((context)->uc_mcontext.gregs[REG_RIP])
#else
#include <darling/emulation/common/base.h>
#include <darling/emulation/linux_premigration/linux-syscalls/linux.h>
#include <darling/emulation/linux_premigration/signal/sigexc.h>
#define DEARUST_RPC_PC(context) ((context)->uc_mcontext.gregs.rip)
#endif
unsigned long dearust_rpc_generation;
extern char dearust_rpc_leaf_begin, dearust_rpc_leaf_end, dearust_rpc_leaf_cancel;
void dearust_rpc_fork_child(struct linux_ucontext *context) {
    unsigned long previous = __atomic_fetch_add(&dearust_rpc_generation, 1, __ATOMIC_RELAXED);
    if (previous == ~0UL) {
        /* Never let an epoch wrap make an abandoned transaction look current. */
        LINUX_SYSCALL(__NR_exit_group, 127);
        __builtin_trap();
    }
    unsigned long pc = DEARUST_RPC_PC(context);
    if (pc >= (unsigned long)&dearust_rpc_leaf_begin && pc < (unsigned long)&dearust_rpc_leaf_end)
        DEARUST_RPC_PC(context) = (unsigned long)&dearust_rpc_leaf_cancel;
}
