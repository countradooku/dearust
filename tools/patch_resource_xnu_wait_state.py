#!/usr/bin/env python3
"""Keep XNU logical wait state independent of native microthread dispatch.

assert_wait/thread_unblock/start/sigexc/death own XNU state transitions. Entering
or leaving a C++ worker is not an XNU wake or cancellation; it may merely be a
nested dtape-mutex suspension within an already registered XNU wait.
"""

ENTER = '''void dtape_thread_entering(dtape_thread_t* thread) {
    // Native dispatch does not complete an XNU wait. A dtape mutex can suspend
    // and reenter between assert_wait and thread_block. The XNU scheduler owns
    // TH_WAIT/TH_UNINT/TH_RUN and wait_result under its own interlocks; touching
    // them here would also race wakeups while the C++ Thread mutex is held.
    (void)thread;
};'''
EXIT = '''void dtape_thread_exiting(dtape_thread_t* thread) {
    // Guest execution and dtape mutex yields do not change logical XNU state.
    // Actual XNU waits and wakes make their own locked state transitions.
    (void)thread;
};'''


def patch_xnu_wait_state(files):
    files = dict(files)
    source = files['src/thread.c']
    old_enter = '''void dtape_thread_entering(dtape_thread_t* thread) {
\t// if the thread is entering, it cannot be waiting
\tthread->xnu_thread.state &= ~(TH_WAIT | TH_UNINT);
\tthread->xnu_thread.state |= TH_RUN;
\tthread->xnu_thread.block_hint = kThreadWaitNone;
};'''
    old_exit = '''void dtape_thread_exiting(dtape_thread_t* thread) {
\tthread->xnu_thread.state &= ~TH_RUN;
};'''
    for old, new in ((old_enter, ENTER), (old_exit, EXIT)):
        if source.count(old) != 1:
            raise ValueError('XNU wait state dispatcher anchor changed')
        source = source.replace(old, new, 1)
    files['src/thread.c'] = source
    return files
