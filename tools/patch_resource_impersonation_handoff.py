#!/usr/bin/env python3
"""Exclusive impersonation claims, independent external deferral and final handoff.

Post-transform after resource_work_handoff. Foreign C operations and continuation
ownership are unchanged. The external deferral enum is never owned or cleared by
an impersonation claim; every claim release owns the same nonallocating scheduler
and terminal cleanup duties as a completed doWork invocation.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('impersonation handoff anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def replace_method(text, start, following, replacement):
    begin = text.index(start)
    end = text.index(following, begin)
    return text[:begin] + replacement + '\n\n' + text[end:]


IMPERSONATE = '''void DarlingServer::Thread::impersonate(std::shared_ptr<Thread> thread) {
    std::shared_ptr<Thread> oldThread;
    if (thread) {
        std::unique_lock lock(thread->_rwlock);
        // An impersonation claim is exclusive and independent of any external
        // defer(). A waiting claimant must recheck ownership after each wake.
        thread->_runningCondvar.wait(lock, [&]() {
            return (!thread->_running && !thread->_impersonationActive) ||
                thread->_dead || thread->_terminating;
        });
        if (thread->_dead || thread->_terminating)
            throw std::system_error(ESRCH, std::generic_category(), "impersonate dead thread");
        thread->_impersonationActive = true;
        thread->_running = true;
    }
    if (thread) thread->_runningCondvar.notify_all();
    {
        std::unique_lock lock(_rwlock);
        oldThread = std::move(_impersonating);
        _impersonating = std::move(thread);
    }
    if (oldThread) {
        bool canRelease = false;
        {
            std::unique_lock lock(oldThread->_rwlock);
            if (!oldThread->_impersonationActive || !oldThread->_running)
                std::terminate();
            oldThread->_impersonationActive = false;
            oldThread->_running = false;
            if (oldThread->_dead && !oldThread->_preparedDeathInProgress &&
                    !oldThread->_activeCall && !oldThread->_terminating) {
                oldThread->_terminating = true;
                canRelease = true;
            }
            if (oldThread->_deferralState != DeferralState::NotDeferred) {
                if (oldThread->_workHandoffPending) {
                    oldThread->_workHandoffPending = false;
                    oldThread->_deferralState = DeferralState::DeferredPending;
                }
            } else {
                oldThread->_handoffPendingWorkLocked();
            }
        }
        oldThread->_runningCondvar.notify_all();
        // Cleanup can synchronously dispose the C context and reacquire its
        // mutex. Never run it under either Thread's C++ mutex.
        if (canRelease) oldThread->_scheduleRelease();
    }
};'''

UNDEFER = '''void DarlingServer::Thread::_undeferLocked(std::unique_lock<std::shared_mutex>& lock) {
    (void)lock;
    const auto previous = _deferralState;
    _deferralState = DeferralState::NotDeferred;
    if (previous == DeferralState::DeferredPending) _workHandoffPending = true;
    // A running invocation or exclusive claimant owns the eventual handoff.
    if (!_running && !_impersonationActive) _handoffPendingWorkLocked();
};'''


def patch_impersonation_handoff(files):
    files = dict(files)
    files['thread.hpp'] = once(files['thread.hpp'],
        '        bool _workHandoffPending = false;',
        '        bool _workHandoffPending = false;\n'
        '        bool _impersonationActive = false;')
    source = files['thread.cpp']
    source = once(source, '\tif (_deferralState != DeferralState::NotDeferred) {',
        '''    if (_impersonationActive) {
        _workHandoffPending = true;
        _rwlock.unlock();
        return;
    }
\tif (_deferralState != DeferralState::NotDeferred) {''')
    source = replace_method(source, 'void DarlingServer::Thread::impersonate(',
        'std::shared_ptr<DarlingServer::Thread> DarlingServer::Thread::impersonatingThread()', IMPERSONATE)
    source = replace_method(source, 'void DarlingServer::Thread::_undeferLocked(',
        'void DarlingServer::Thread::undefer()', UNDEFER)
    source = once(source, '    if (canRelease) _scheduleRelease();\n    else resume();',
        '    _runningCondvar.notify_all();\n    if (canRelease) _scheduleRelease();\n    else resume();')
    files['thread.cpp'] = source
    return files


CALL_ERROR = '''namespace {
int preparedImpersonationError() noexcept {
    try { throw; }
    catch (const std::system_error& error) {
        const int code = error.code().value();
        return -(code > 0 ? code : EIO);
    }
    catch (const std::bad_alloc&) { return -ENOMEM; }
    catch (...) { return -EIO; }
}
}
'''

MODIFY = '''void DarlingServer::Kqchan::MachPort::_modify(uint64_t receiveBuffer, uint64_t receiveBufferSize,
        uint64_t savedFilterFlags, pid_t nstid) {
    auto maybeThread = threadRegistry().lookupEntryByNSID(nstid);
    if (!maybeThread) { _completePreparedMachModify(-ESRCH); return; }
    auto self = shared_from_this();
    try {
        Thread::kernelAsync([self, thread = *maybeThread, receiveBuffer, receiveBufferSize, savedFilterFlags]() {
            auto worker = Thread::currentThread();
            bool claimed = false;
            int error = 0;
            try {
                worker->impersonate(thread);
                claimed = true;
                dtape_kqchan_mach_port_modify(self->_dtapeKqchan, receiveBuffer, receiveBufferSize, savedFilterFlags);
            } catch (...) { error = preparedImpersonationError(); }
            if (claimed) worker->impersonate(nullptr);
            if (!error && dtape_kqchan_mach_port_has_events(self->_dtapeKqchan)) self->_notify();
            self->_completePreparedMachModify(error);
        });
    } catch (...) { _completePreparedMachModify(preparedImpersonationError()); }
}
'''
READ = '''void DarlingServer::Kqchan::MachPort::_read(uint64_t defaultBuffer, uint64_t defaultBufferSize, pid_t nstid) {
    auto maybeThread = threadRegistry().lookupEntryByNSID(nstid);
    if (!maybeThread) {
        dserver_kqchan_reply_mach_port_read_t reply = {};
        reply.header.number = dserver_kqchan_msgnum_mach_port_read;
        reply.header.code = -ESRCH;
        _completePreparedMachRead(reply);
        return;
    }
    auto self = shared_from_this();
    try {
        Thread::kernelAsync([self, thread = *maybeThread, defaultBuffer, defaultBufferSize]() {
            dserver_kqchan_reply_mach_port_read_t reply = {};
            reply.header.number = dserver_kqchan_msgnum_mach_port_read;
            auto worker = Thread::currentThread();
            bool claimed = false;
            try {
                worker->impersonate(thread);
                claimed = true;
                if (!dtape_kqchan_mach_port_fill(self->_dtapeKqchan, &reply, defaultBuffer, defaultBufferSize))
                    reply.header.code = 0xdead;
            } catch (...) { reply.header.code = preparedImpersonationError(); }
            if (claimed) worker->impersonate(nullptr);
            if ((reply.header.code == 0 || reply.header.code == 0xdead) && dtape_kqchan_mach_port_has_events(self->_dtapeKqchan)) self->_notify();
            self->_completePreparedMachRead(reply);
        });
    } catch (...) {
        dserver_kqchan_reply_mach_port_read_t reply = {};
        reply.header.number = dserver_kqchan_msgnum_mach_port_read;
        reply.header.code = preparedImpersonationError();
        _completePreparedMachRead(reply);
    }
}
'''


def patch_impersonation_callers(files):
    """Contain retired targets and queue/preparation failures in real error replies."""
    files = dict(files)
    files['kqchan.hpp'] = once(files['kqchan.hpp'],
        'void _completePreparedMachModify() noexcept;',
        'void _completePreparedMachModify(int error = 0) noexcept;')
    source = files['kqchan.cpp']
    source = once(source,
        'void DarlingServer::Kqchan::MachPort::_completePreparedMachModify() noexcept {',
        'void DarlingServer::Kqchan::MachPort::_completePreparedMachModify(int error) noexcept {')
    source = once(source,
        '        _preparedMachReply.modify.header.number = dserver_kqchan_msgnum_mach_port_modify;',
        '        _preparedMachReply.modify.header.number = dserver_kqchan_msgnum_mach_port_modify;\n'
        '        _preparedMachReply.modify.header.code = error;')
    source = replace_method(source, 'void DarlingServer::Kqchan::MachPort::_modify(',
        'void DarlingServer::Kqchan::MachPort::_read(', CALL_ERROR + '\n' + MODIFY)
    source = replace_method(source, 'void DarlingServer::Kqchan::MachPort::_read(',
        'bool DarlingServer::Kqchan::MachPort::_processPreparedTransportEvent(', READ)
    files['kqchan.cpp'] = source
    return files


def patch_impersonation(files):
    """Production entry point: compose the claim and required caller containment."""
    return patch_impersonation_callers(patch_impersonation_handoff(files))
