#!/usr/bin/env python3
"""Retain scheduler requests that race an already-running microthread.

Apply after reserved dispatch/death work. The existing worker owns the handoff
until doneWorking clears _running under the same Thread lock. No duplicate spins
or polls the running microthread and all handoffs use its embedded reserved node.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('work handoff anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


HANDOFF = '''
void DarlingServer::Thread::_handoffPendingWorkLocked() noexcept {
    if (!_workHandoffPending) return;
    _workHandoffPending = false;
    if (_running) std::terminate();
    if (_terminating || (_dead && !_activeCall)) return;
    auto owner = _dead ? _selfReference : shared_from_this();
    if (!owner) std::terminate();
    Server::sharedInstance().scheduleResumedThread(std::move(owner));
}
'''


def patch_work_handoff(files):
    files = dict(files)
    files['thread.hpp'] = once(files['thread.hpp'], '\t\tbool _running = false;',
        '\t\tbool _running = false;\n        bool _workHandoffPending = false;\n'
        '        void _handoffPendingWorkLocked() noexcept;')
    source = files['thread.cpp']
    source = once(source, '    if (_preparedDeathInProgress) return;',
                  '    if (_preparedDeathInProgress || _running) return;')
    source = once(source, '        canRelease = !_activeCall;',
                  '        canRelease = !_activeCall && !_running;')
    begin = source.index('void DarlingServer::Thread::doWork() {')
    end = source.index('void DarlingServer::Thread::suspend(', begin)
    work = source[begin:end]
    kernel_unlock = '''\tif (unlockMeWhenSuspending) {
\t\tlibsimple_lock_unlock(unlockMeWhenSuspending);
\t\tunlockMeWhenSuspending = nullptr;
\t}
'''
    work = once(work, kernel_unlock, '')
    work = once(work, 'doneWorking:\n\t// we must be holding `_rwlock` when we get here\n',
        'doneWorking:\n\t// we must be holding `_rwlock` when we get here\n'
        '    // Complete the kernel suspend handshake before another worker can\n'
        '    // observe _running=false and enter this retained C context. The\n'
        '    // host libsimple unlock only releases an atomic/futex interlock.\n' + kernel_unlock)
    work = once(work, '''\tif (_running) {
\t\t// this is probably an error
\t\t_rwlock.unlock();
\t\treturn;
\t}''', '''\tif (_running) {
        // The prior invocation can have sent its reply before reaching its
        // scheduler epilogue. Preserve this request for that invocation to
        // hand off after clearing _running; retrying now would only spin.
        _workHandoffPending = true;
\t\t_rwlock.unlock();
\t\treturn;
\t}''')
    work = once(work, '\tif (_terminating && !_dead) {',
        '\tif (_terminating && !_dead) {\n        _workHandoffPending = false;')
    work = once(work, '\t\t\tServer::sharedInstance().scheduleThread(shared_from_this());',
        '''            // Fold an interrupt and any concurrently observed request
            // into the same reserved handoff, without a second queued task.
            _workHandoffPending = true;''')
    work = once(work, '\n\t\t_rwlock.unlock();\n\t}\n\t_runningCondvar.notify_all();',
        '\n        _handoffPendingWorkLocked();\n\t\t_rwlock.unlock();\n\t}\n\t_runningCondvar.notify_all();')
    files['thread.cpp'] = source[:begin] + work + source[end:] + '\n' + HANDOFF
    return files
