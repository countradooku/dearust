#!/usr/bin/env python3
"""Checked pre-publication dtape allocation boundaries in owned C copies.

These transforms intentionally stop before IPC initialization. They do not call
normal destruction on partially initialized task/thread objects or reinterpret
XNU panic/no-fail APIs as ordinary errors. Use exact selected candidate sources
so existing lockset callbacks and other bridges survive rebuilding.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('inner dtape anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_task(text):
    anchor = '\ttask->xnu_task.map = dtape_vm_map_create(task);'
    return once(text, anchor, anchor + '''
    if (!task->xnu_task.map) {
        // No IPC space, ports, importance, psynch table or task-list ownership
        // exists yet. The initialized locks/condition queues own no storage.
        // The caller has not received a task handle/context reference.
        lck_mtx_destroy(&task->xnu_task.lock, LCK_GRP_NULL);
        free(task);
        return NULL;
    }''')


def patch_thread(text):
    helper = '''static void dearust_dtape_thread_retire_active(dtape_thread_t* thread) {
    bool wake = false;
    thread_lock(&thread->xnu_thread);
    if (thread->xnu_thread.active) {
        thread->xnu_thread.active = false;
        wake = os_atomic_dec(&thread->xnu_thread.task->active_thread_count, relaxed) == 0;
    }
    thread_unlock(&thread->xnu_thread);
    if (wake) thread_wakeup((event_t)&thread->xnu_thread.task->active_thread_count);
}

'''
    text = once(text, 'dtape_thread_t* dtape_thread_create(',
                'extern struct turnstile* dearust_dtape_turnstile_try_alloc(void);\n\n'
                + helper + 'dtape_thread_t* dtape_thread_create(')
    anchor = '\tipc_thread_init(&thread->xnu_thread, IPC_THREAD_INIT_NONE);'
    text = once(text, anchor, '''    // Reserve the fallible turnstile before IPC ports/task reference/list
    // attachment. A NULL result can then release only the unexposed wrapper.
    thread->xnu_thread.turnstile = dearust_dtape_turnstile_try_alloc();
    if (!thread->xnu_thread.turnstile) {
        lck_mtx_destroy(&thread->xnu_thread.mutex, LCK_GRP_NULL);
        free(thread);
        return NULL;
    }

''' + anchor)
    text = once(text, '\tthread->xnu_thread.turnstile = turnstile_alloc();\n', '')
    for function in ('dtape_thread_dying', 'dtape_thread_destroy'):
        anchor = f'void {function}(dtape_thread_t* thread) {{'
        text = once(text, anchor, anchor + '\n    dearust_dtape_thread_retire_active(thread);')
    return text


def patch_turnstile(text):
    # Keep generic XNU turnstile_alloc's existing contract and all other callers
    # unchanged. The dtape-specific checked entry shares the exact initialization
    # and debug-list publication, adding a NULL check before dereferencing storage.
    start = text.index('struct turnstile *\nturnstile_alloc(void)\n{')
    end = text.index('\n}\n', start) + len('\n}\n')
    function = text[start:end]
    function = once(function, 'turnstile_alloc(void)', 'dearust_dtape_turnstile_try_alloc(void)')
    function = once(function, '\tturnstile = zalloc(turnstiles_zone);',
                    '\tturnstile = zalloc(turnstiles_zone);\n'
                    '\tif (turnstile == TURNSTILE_NULL) return TURNSTILE_NULL;')
    return text[:end] + '\n/* Checked dtape-only allocation before thread publication. */\n' + function + text[end:]


def patch_inner_dtape(files):
    """Return a mapping keyed by actual duct-tape archive source paths."""
    files = dict(files)
    files['src/task.c'] = patch_task(files['src/task.c'])
    files['src/thread.c'] = patch_thread(files['src/thread.c'])
    files['xnu/osfmk/kern/turnstile.c'] = patch_turnstile(files['xnu/osfmk/kern/turnstile.c'])
    return files
