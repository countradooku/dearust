/* Early signal-context repair for the bounded guest-copy assembly.  This header
 * is injected only into copied original Darling signal translation units. */
#ifndef DEARUST_COPY_SIGNAL_BRIDGE_H
#define DEARUST_COPY_SIGNAL_BRIDGE_H
#if defined(__x86_64__)
#ifdef __APPLE__
#include <darling/emulation/common/base.h>
#include <darling/emulation/linux_premigration/linux-syscalls/linux.h>
#endif
/* Failure to establish process identity cannot be treated as a valid PID:
 * otherwise a policy-denied getpid could conceal a handler fork. */
static inline long dearust_signal_process_identity(void)
{
    long identity = LINUX_SYSCALL(__NR_getpid);
    if (identity <= 0) {
        LINUX_SYSCALL(__NR_exit_group, 127);
        __builtin_trap();
    }
    return identity;
}
extern unsigned long dearust_copy_signal_fixup(unsigned long pc, int signal, int code);
static inline int dearust_copy_signal_enter(int signal, struct linux_siginfo *info,
                                           struct linux_ucontext *context)
{
    unsigned long next = dearust_copy_signal_fixup(context->uc_mcontext.gregs.rip,
                                                  signal, info->si_code);
    if (!next)
        return 0;
    context->uc_mcontext.gregs.rip = next;
    /* Only actual synchronous copy faults are consumed.  Async signals retain
     * the original delivery path, with an interrupted copy redirected to retry. */
    return (signal == 11 || signal == 7) && info->si_code > 0;
}
#endif
#endif
