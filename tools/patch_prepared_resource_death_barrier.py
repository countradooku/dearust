#!/usr/bin/env python3
"""Keep prepared cleanup behind completion of the C thread-dying notification."""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared death barrier anchor is not unique: ' + old[:100])
    return text.replace(old, new, 1)


NOTIFY_DEAD = '''void DarlingServer::Thread::notifyDead() {
    {
        std::unique_lock lock(_rwlock);
        if (_dead) return;
        // Own the context before exposing death to its worker. A completing
        // active call must not enqueue final release until dying has returned.
        _selfReference = shared_from_this();
        _preparedDeathInProgress = true;
        _dead = true;
    }

    // clear_wait may synchronously resume this Thread, so its C++ mutex cannot
    // remain held here. The barrier protects the raw handle across that gap.
    dtape_thread_dying(_dtapeThread);

    bool canRelease;
    {
        std::unique_lock lock(_rwlock);
        _preparedDeathInProgress = false;
        // The active call may have completed while dying was in progress.
        // Re-evaluation also recovers a release deferred by abortKernelHandles.
        canRelease = !_activeCall;
        if (canRelease) _terminating = true;
    }
    if (canRelease) _scheduleRelease();
    else resume();
    threadRegistry().unregisterEntry(shared_from_this());
};
'''


def patch_thread_death_barrier(files):
    files = dict(files)
    files['thread.hpp'] = once(files['thread.hpp'], '\t\tbool _dead = false;',
        '\t\tbool _dead = false;\n\t\tbool _preparedDeathInProgress = false;')
    text = files['thread.cpp']
    begin = text.index('void DarlingServer::Thread::notifyDead() {')
    end = text.index('bool DarlingServer::Thread::isDead() const', begin)
    text = text[:begin] + NOTIFY_DEAD + '\n' + text[end:]
    text = once(text, 'if (_dead && !_activeCall && !_terminating) {',
                'if (_dead && !_preparedDeathInProgress && !_activeCall && !_terminating) {')
    text = once(text, '''\tif (_dead) {
\t\tthreadLog.debug() << *this << ": dead thread returning. active call? " << (!!_activeCall ? "true" : "false") << " terminating? " << (_terminating ? "true" : "false") << threadLog.endLog;
\t}
''', '')
    text = once(text, '    if (_resourceCleanupQueued) return;',
        '    if (_preparedDeathInProgress) return;\n    if (_resourceCleanupQueued) return;')
    files['thread.cpp'] = text
    return files
