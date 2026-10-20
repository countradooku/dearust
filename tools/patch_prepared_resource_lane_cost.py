#!/usr/bin/env python3
"""Experimental owned cleanup lane: preserve default stack, avoid C++ wrapper heap.

This does not shrink the native stack or change the global allocator policy.
The server-owned pthread remains live for the server process lifetime, as did
the original intentionally persistent std::thread and its state.
"""
import re
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('cleanup lane cost anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_lane_cost(files):
    files = dict(files)
    text = files['thread.cpp']
    text = once(text, '    std::thread* native = nullptr;', '    pthread_t native{};')
    old = '''        state->native = new std::thread([state]() {
            preparedResourceCleanupSuppressLog = true;
            for (;;) {
                state->thread->doWork();
                preparedResourceCleanupCheck(pthread_mutex_lock(&state->lock));
                while (!state->resume)
                    preparedResourceCleanupCheck(pthread_cond_wait(&state->changed, &state->lock));
                state->resume = false;
                preparedResourceCleanupCheck(pthread_mutex_unlock(&state->lock));
            }
        });'''
    new = '''        // Null attributes preserve the same platform-default native stack as
        // std::thread. All substantive C/XNU cleanup still runs on the existing
        // guarded microthread stack, with unchanged suspension/resume behavior.
        const int nativeError = pthread_create(&state->native, nullptr, [](void* opaque) -> void* {
            auto* state = static_cast<PreparedResourceCleanupState*>(opaque);
            preparedResourceCleanupSuppressLog = true;
            for (;;) {
                state->thread->doWork();
                preparedResourceCleanupCheck(pthread_mutex_lock(&state->lock));
                while (!state->resume)
                    preparedResourceCleanupCheck(pthread_cond_wait(&state->changed, &state->lock));
                state->resume = false;
                preparedResourceCleanupCheck(pthread_mutex_unlock(&state->lock));
            }
        }, state);
        if (nativeError) throw std::system_error(nativeError, std::generic_category(), "start cleanup pthread");'''
    text = once(text, old, new)
    text = once(text, 'static thread_local ucontext_t backToThreadTopContext;',
                'static thread_local ucontext_t backToThreadTopContext = {};')
    # Nontrivial C++ TLS first use registers a destructor with libc. The lane
    # never exits and already has process-lifetime startup-owned state; place
    # only its current microthread/continuation owners there. Other native
    # workers retain their original TLS ownership and thread-exit destruction.
    text = once(text, '    pthread_t native{};', '''    pthread_t native{};
    std::shared_ptr<DarlingServer::Thread> currentThread;
    std::function<void()> continuation;
    std::function<void()> interruptedOwner;''')
    text = once(text, 'static thread_local bool preparedResourceCleanupSuppressLog = false;', '''static thread_local bool preparedResourceCleanupSuppressLog = false;
static thread_local std::shared_ptr<DarlingServer::Thread>* cleanupCurrentOwner = nullptr;
static thread_local std::function<void()>* cleanupContinuationOwner = nullptr;
static thread_local std::function<void()>* cleanupInterruptedOwner = nullptr;''')
    text = once(text, '            auto* state = static_cast<PreparedResourceCleanupState*>(opaque);', '''            auto* state = static_cast<PreparedResourceCleanupState*>(opaque);
            cleanupCurrentOwner = &state->currentThread;
            cleanupContinuationOwner = &state->continuation;
            cleanupInterruptedOwner = &state->interruptedOwner;''')
    for old_name, ordinary_name, owner in (
        ('currentThreadVar', 'ordinaryThreadCurrent', 'cleanupCurrentOwner'),
        ('currentContinuation', 'ordinaryThreadContinuation', 'cleanupContinuationOwner'),
        ('interruptedContinuation', 'ordinaryThreadInterruptedContinuation', 'cleanupInterruptedOwner'),
    ):
        declaration = re.search(r'static thread_local [^;\n]+\b' + old_name + r' = nullptr;', text)
        if not declaration:
            raise ValueError('missing ordinary TLS owner: ' + old_name)
        replacement = declaration.group().replace(old_name, ordinary_name)
        text = text[:declaration.start()] + replacement + text[declaration.end():]
        text = re.sub(r'\b' + old_name + r'\b', old_name + 'Storage()', text)
        accessor = f'''
static auto& {old_name}Storage() {{
    // One cached owner per native worker. Ordinary workers initialize their
    // original TLS owner on first use and retain its automatic destruction.
    // The dedicated lane installs startup-owned storage before entering dtape.
    if (!{owner}) {owner} = &{ordinary_name};
    return *{owner};
}}
'''
        if old_name == 'interruptedContinuation':
            # Its ordinary TLS declaration is later in this translation unit.
            text = once(text, replacement, replacement + '\n' + accessor)
        else:
            anchor = 'static void preparedResourceCleanupCheck(int error) noexcept {'
            text = once(text, anchor, accessor + '\n' + anchor)
    files['thread.cpp'] = text
    return files


def patch_lane_sustained_validation(files):
    """Extend only the test derivative with repeated real cleanup and queue load."""
    files = dict(files)
    text = files['server.cpp']
    text = '''extern "C" void dearust_lane_mutex_initialize();
extern "C" void dearust_lane_mutex_lock();
extern "C" void dearust_lane_mutex_unlock();
static std::atomic<unsigned> laneForcedSuspensions{0};
''' + text
    suspend = '\tstatic void dtape_hook_thread_suspend(void* thread_context, dtape_thread_continuation_callback_f continuationCallback, void* continuationContext, libsimple_lock_t* unlockMe) {'
    text = once(text, suspend, suspend + '''
        if (DarlingServer::Thread::resourceCleanupLoggingSuppressed()) ++laneForcedSuspensions;''')
    text = once(text, '\t_runActualPreparedCleanupValidation();', '''
    for (unsigned round = 0; round < 64; ++round)
        _runActualPreparedCleanupValidation();
    _Exit(0);''')
    # Every prior round waits for expired weak ownership and exact descriptor/
    # epoll restoration, so these test-only disposal counters can start afresh.
    text = once(text, 'void DarlingServer::Server::_runActualPreparedCleanupValidation() {',
        'void DarlingServer::Server::_runActualPreparedCleanupValidation() {\n'
        '    actualCleanupTaskDisposals = 0;\n    actualCleanupThreadDisposals = 0;\n' +
        Path(__file__).with_name('prepared-resource-lane-sustained.inc').read_text())
    # Remove only the lifecycle test's successful terminal exit, preserving all
    # test failure exits and the loop's final process exit.
    text = once(text, '    _Exit(0);\n}', '    return;\n}')
    files['server.cpp'] = '#include <atomic>\n#include <thread>\n#include <vector>\n' + text
    return files
