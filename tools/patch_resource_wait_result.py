#!/usr/bin/env python3
"""Preserve an interruption committed before removal of a linked XNU wait."""


def patch_wait_result(thread_c: str) -> str:
    start = thread_c.index('boolean_t thread_unblock(thread_t xthread, wait_result_t wresult) {')
    end = thread_c.index('\n};', start)
    body = thread_c[start:end]
    anchor = '\tthread->xnu_thread.wait_result = wresult;'
    if body.count(anchor) != 1:
        raise ValueError('expected one selected thread_unblock result assignment')
    body = body.replace(anchor, '''\t// The caller holds the XNU thread lock. sigexc/dying may already have
\t// committed interruption while their queue-removal retry dropped that
\t// lock. A later queue-first wake must complete removal/resume without
\t// replacing the winning result.
\tconst bool interrupted = thread->xnu_thread.wait_result == THREAD_INTERRUPTED &&
\t\t(!(thread->xnu_thread.state & TH_WAIT) ||
\t\t (thread->xnu_thread.state & TH_TERMINATE));
\tif (!interrupted) thread->xnu_thread.wait_result = wresult;''', 1)
    return thread_c[:start] + body + thread_c[end:]
