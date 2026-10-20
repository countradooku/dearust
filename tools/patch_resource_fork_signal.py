#!/usr/bin/env python3
"""Retain the XNU fork semaphore before leaving its native Process mutex.

A C semaphore signal can yield and resume on another native worker. No native
C++ mutex may span that operation. The XNU structure reference survives wrapper
retirement; deactivation racing the signal is a normal terminal outcome.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('fork semaphore anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


DECLARATIONS = '''
extern "C" void* dearust_dtape_semaphore_pin(dtape_semaphore_t*);
extern "C" void dearust_dtape_semaphore_signal_and_unpin(void*);
'''

BEFORE = '''            {
                std::unique_lock parentLock(parent->_rwlock);
                if (!parent->_dead && !parent->_resourceCleanupQueued &&
                        parent->_dtapeForkWaitSemaphore)
                    dtape_semaphore_up(parent->_dtapeForkWaitSemaphore);
            }
'''
AFTER = '''            void* forkSignal = nullptr;
            {
                std::shared_lock parentLock(parent->_rwlock);
                if (!parent->_dead && !parent->_resourceCleanupQueued &&
                        parent->_dtapeForkWaitSemaphore)
                    forkSignal = dearust_dtape_semaphore_pin(parent->_dtapeForkWaitSemaphore);
            }
            // Signaling may suspend and resume on another native worker.
            // Only the referenced XNU object crosses that yield, never a
            // native mutex or an unowned dtape wrapper pointer.
            if (forkSignal) dearust_dtape_semaphore_signal_and_unpin(forkSignal);
'''

C_HELPERS = '''
// semaphore_reference is an atomic refcount retain. The caller holds the
// wrapper owner's mutex only during this nonblocking lifetime acquisition.
void* dearust_dtape_semaphore_pin(dtape_semaphore_t* semaphore) {
    if (!semaphore) return NULL;
    semaphore_t pinned = semaphore->xnu_semaphore;
    semaphore_reference(pinned);
    return pinned;
}

// Consume the acquired reference even when concurrent owner teardown has
// deactivated the semaphore. Deactivation does not invalidate a retained
// XNU structure; no dtape wrapper or owning-task pointer is dereferenced.
void dearust_dtape_semaphore_signal_and_unpin(void* reference) {
    semaphore_t pinned = (semaphore_t)reference;
    kern_return_t result = semaphore_signal(pinned);
    semaphore_dereference(pinned);
    if (result != KERN_SUCCESS && result != KERN_TERMINATED)
        panic("Failed to signal retained fork semaphore");
}
'''


def patch_fork_signal_cpp(files):
    files = dict(files)
    text = once(files['process.cpp'], 'void DarlingServer::Process::notifyCheckin(Architecture architecture) {',
        DECLARATIONS + '\nvoid DarlingServer::Process::notifyCheckin(Architecture architecture) {')
    files['process.cpp'] = once(text, BEFORE, AFTER)
    return files


def patch_fork_signal_c(files):
    files = dict(files)
    source = files['src/semaphore.c']
    if 'dearust_dtape_semaphore_pin' in source:
        raise ValueError('retained fork semaphore already present')
    if '#include <darlingserver/duct-tape/semaphore.h>' not in source:
        raise ValueError('selected semaphore wrapper header missing')
    files['src/semaphore.c'] = source + '\n' + C_HELPERS
    return files
