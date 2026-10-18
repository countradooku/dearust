/* Native-only signal-context adapter. Never linked into a guest runtime. */
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdlib.h>
#include <ucontext.h>
#include <unistd.h>

extern unsigned long dearust_copy_signal_fixup(unsigned long, int, int);
static struct sigaction previous[NSIG];
static _Atomic int ready;

int dearust_copy_signals_ready(void)
{
    return atomic_load_explicit(&ready, memory_order_acquire);
}

static void handler(int signal, siginfo_t *info, void *opaque)
{
    ucontext_t *context = opaque;
    unsigned long next = dearust_copy_signal_fixup(
        (unsigned long)context->uc_mcontext.gregs[REG_RIP], signal, info->si_code);
    if (next) {
        context->uc_mcontext.gregs[REG_RIP] = (greg_t)next;
        if ((signal == SIGSEGV || signal == SIGBUS) && info->si_code > 0)
            return;
    }
    struct sigaction action = previous[signal];
    if (action.sa_handler == SIG_IGN)
        return;
    if (action.sa_handler == SIG_DFL) {
        /* Restore default then queue the signal. It becomes deliverable when
         * this wrapper returns and the kernel restores the interrupted mask. */
        (void)sigaction(signal, &action, NULL);
        (void)raise(signal);
        return;
    }
    if (action.sa_flags & SA_RESETHAND)
        previous[signal].sa_handler = SIG_DFL;
    if (action.sa_flags & SA_SIGINFO)
        action.sa_sigaction(signal, info, opaque);
    else
        action.sa_handler(signal);
}

/* Tests must use this instead of replacing the early PC-repair wrapper. Calls
 * occur outside signal delivery, before starting test workers. */
int dearust_copy_test_set_handler(int signal, const struct sigaction *action,
                                 struct sigaction *old_action)
{
    if (signal <= 0 || signal >= NSIG || signal == SIGKILL || signal == SIGSTOP) {
        errno = EINVAL;
        return -1;
    }
    if (old_action)
        *old_action = previous[signal];
    if (!action)
        return 0;
    struct sigaction wrapped = *action;
    wrapped.sa_sigaction = handler;
    wrapped.sa_flags = (wrapped.sa_flags | SA_SIGINFO) & ~SA_RESETHAND;
    struct sigaction saved = previous[signal];
    previous[signal] = *action;
    if (sigaction(signal, &wrapped, NULL) < 0) {
        previous[signal] = saved;
        return -1;
    }
    return 0;
}

__attribute__((constructor)) static void install_copy_signals(void)
{
    const int signals[] = {SIGSEGV, SIGBUS, SIGUSR1, SIGUSR2, SIGALRM, SIGCHLD, SIGTTOU};
    for (size_t index = 0; index < sizeof(signals) / sizeof(signals[0]); ++index) {
        int signal = signals[index];
        if (sigaction(signal, NULL, &previous[signal]) < 0)
            abort();
        struct sigaction wrapped = previous[signal];
        wrapped.sa_sigaction = handler;
        wrapped.sa_flags = (wrapped.sa_flags | SA_SIGINFO) & ~SA_RESETHAND;
        if (sigaction(signal, &wrapped, NULL) < 0)
            abort();
    }
    atomic_store_explicit(&ready, 1, memory_order_release);
}
