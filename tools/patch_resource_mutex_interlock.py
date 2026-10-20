#!/usr/bin/env python3
"""Serialize active dtape mutex try-lock with the native queue interlock.

Owner diagnostics use atomic accesses without changing dtape_mutex_t layout.
This does not change the existing no-active-thread native busy-spin fallback.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('mutex interlock anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


OWNER = '''
// The queue interlock orders ownership transitions. Atomic owner observations
// also make assertions and waitq diagnostics outside that interlock race-free;
// they do not acquire the mutex or prove an object's lifetime.
static uintptr_t dearust_mutex_owner(const dtape_mutex_t* mutex) {
    return __atomic_load_n(&mutex->dtape_owner, __ATOMIC_RELAXED);
}
static void dearust_mutex_set_owner(dtape_mutex_t* mutex, uintptr_t owner) {
    __atomic_store_n(&mutex->dtape_owner, owner, __ATOMIC_RELAXED);
}
'''


def patch_mutex_interlock(text):
    begin = text.index('bool dtape_mutex_try_lock(dtape_mutex_t* mutex) {')
    end = text.index('void dtape_mutex_unlock(', begin)
    attempt = text[begin:end]
    attempt = once(attempt, '\tdtape_mutex_assert(mutex, false);\n',
        '''\tdtape_mutex_assert(mutex, false);
    // Never inspect or change ownership without owning the queue interlock.
    // A failed try-acquire must not unlock another native worker's interlock.
    if (!libsimple_lock_try_lock(&mutex->dtape_queue_lock)) return FALSE;
''')
    attempt = once(attempt, '\t// lock not acquired\n\treturn FALSE;',
        '\t// Another microthread owns the mutex; release only our interlock.\n'
        '\tlibsimple_lock_unlock(&mutex->dtape_queue_lock);\n\treturn FALSE;')
    text = text[:begin] + attempt + text[end:]
    if text.count('mutex->dtape_owner = 0;') != 2:
        raise ValueError('expected init and unlock owner stores')
    if text.count('mutex->dtape_owner = (uintptr_t)xthread;') != 2:
        raise ValueError('expected lock and try-lock owner stores')
    text = text.replace('mutex->dtape_owner = 0;', 'dearust_mutex_set_owner(mutex, 0);')
    text = text.replace('mutex->dtape_owner = (uintptr_t)xthread;',
                        'dearust_mutex_set_owner(mutex, (uintptr_t)xthread);')
    text = text.replace('mutex->dtape_owner', 'dearust_mutex_owner(mutex)')
    text = once(text, 'lock->dtape_mutex.dtape_owner != 0',
                'dearust_mutex_owner(&lock->dtape_mutex) != 0')
    return once(text, 'void dtape_mutex_init(dtape_mutex_t* mutex) {',
                OWNER + '\nvoid dtape_mutex_init(dtape_mutex_t* mutex) {')


def patch_mutex_misc(text):
    field = 'wq->dtape_waitq_interlock.dtape_interlock.dtape_interlock.dtape_mutex.dtape_owner'
    return once(text, field + ' == (uintptr_t)current_thread()',
        '__atomic_load_n(&' + field + ', __ATOMIC_RELAXED) == (uintptr_t)current_thread()')


def patch_mutex_interlock_files(files):
    files = dict(files)
    files['src/locks.c'] = patch_mutex_interlock(files['src/locks.c'])
    files['src/misc.c'] = patch_mutex_misc(files['src/misc.c'])
    return files
