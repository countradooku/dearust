#!/usr/bin/env python3
"""Transform owned server sources for prepared, intrusive kernel cleanup.

Input/output is a mapping keyed by basename. Call after existing resource/FD
transforms and before prepared registry/lifecycle transforms. Copy helper_files()
to the owned include path; no reference or existing candidate is changed here.
"""
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared cleanup anchor not unique: ' + old[:120])
    return text.replace(old, new, 1)


def helper_files():
    return [Path(__file__).with_name('prepared-resource-cleanup.hpp')]


def patch_cleanup(files):
    files = dict(files)
    for name, handle in [('process.hpp', 'dtape_task_t* _dtapeTask;'),
                         ('thread.hpp', 'dtape_thread_t* _dtapeThread;')]:
        text = '#include "prepared-resource-cleanup.hpp"\n' + files[name]
        text = once(text, handle, handle[:-1] + ' = nullptr;')
        if name == 'process.hpp':
            text = once(text, 'dtape_semaphore_t* _dtapeForkWaitSemaphore;',
                        'dtape_semaphore_t* _dtapeForkWaitSemaphore = nullptr;')
        text = once(text, '\t\tvoid _dispose();', '''\t\tvoid _dispose();
        PreparedResourceCleanupJob _resourceCleanupJob;
        bool _resourceCleanupQueued = false;
        static void _resourceCleanupRun(void* context) noexcept;
    public:
        void prepareKernelHandles();
        void abortKernelHandles() noexcept;
    private:''')
        if name == 'thread.hpp':
            text = once(text, '\t\tstatic void kernelAsync(std::function<void()> fn);', '''        static void initializeResourceCleanup();
        static void requireResourceCleanupReady();
        static bool resourceCleanupLane(const Thread* thread) noexcept;
        static bool resourceCleanupLoggingSuppressed() noexcept;
        static void enqueueResourceCleanup(PreparedResourceCleanupJob& job) noexcept;
\t\tstatic void kernelAsync(std::function<void()> fn);''')
        files[name] = text

    text = '#include <new>\n#include <system_error>\n' + files['process.cpp']
    start = text.index('\t// NOTE: see thread.cpp for why it\'s okay to use `this` here')
    end = text.index('\n};', start)
    text = text[:start] + '\t// Kernel handles are acquired after shared ownership and EID reservation.\n' + text[end:]
    kernel = '_dtapeTask = dtape_task_create(nullptr, _nspid, this, static_cast<dserver_rpc_architecture_t>(_architecture));'
    text = once(text, kernel, kernel + '\n\tif (!_dtapeTask) throw std::bad_alloc();')
    start = text.index('\t// schedule the duct-taped task to be released', text.index('void DarlingServer::Process::notifyDead()'))
    end = text.index('\n\n\tprocessRegistry().unregisterEntry', start)
    text = text[:start] + '\tabortKernelHandles();' + text[end:]
    text += '\n' + Path(__file__).with_name('prepared-resource-cleanup-process.inc').read_text()
    files['process.cpp'] = text

    text = '#include <atomic>\n#include <thread>\n#include <new>\n#include <stdexcept>\n#include <system_error>\n' + files['thread.cpp']
    start = text.index('\t// NOTE: it\'s okay to use raw `this` without a shared pointer')
    end = text.index('\n};', start)
    text = text[:start] + '\t// Kernel handles are acquired after shared ownership and EID reservation.\n' + text[end:]
    kernel = '_dtapeThread = dtape_thread_create(Process::kernelProcess()->_dtapeTask, _nstid, this);'
    text = once(text, kernel, kernel + '\n\tif (!_dtapeThread) throw std::bad_alloc();')
    text = once(text, 'void DarlingServer::Thread::resume() {', '''void DarlingServer::Thread::resume() {
    if (resourceCleanupLane(this)) {
        preparedResourceCleanupWake();
        return;
    }''')
    start = text.index('void DarlingServer::Thread::_scheduleRelease() {')
    end = text.index('\nstatic thread_local std::function<void()> interruptedContinuation', start)
    text = text[:start] + '''void DarlingServer::Thread::_scheduleRelease() {
    abortKernelHandles();
};
''' + text[end:]
    text = once(text, '\tthreadLog.debug() << *this << ": dispose thread context" << threadLog.endLog;\n', '')
    # Helpers precede resume(), while the complete Thread definition is already
    # visible through its header. Their standalone source is used by the fixture.
    anchor = 'static DarlingServer::Log threadLog("thread");'
    text = once(text, anchor, anchor + '\n\n' + Path(__file__).with_name('prepared-resource-cleanup-thread.inc').read_text())
    files['thread.cpp'] = text

    text = files['server.cpp']
    text = once(text, '\tThread::kernelSync(dtape_init_in_thread);',
                '\tThread::kernelSync(dtape_init_in_thread);\n\tThread::initializeResourceCleanup();')
    text = once(text, 'static int dtape_hook_log_enabled(dtape_log_level_t level) {',
                'static int dtape_hook_log_enabled(dtape_log_level_t level) {\n'
                '\t\tif (DarlingServer::Thread::resourceCleanupLoggingSuppressed()) return 0;')
    text = once(text, 'static void dtape_hook_log(dtape_log_level_t level, const char* message) {',
                'static void dtape_hook_log(dtape_log_level_t level, const char* message) {\n'
                '\t\tif (DarlingServer::Thread::resourceCleanupLoggingSuppressed()) return;')
    files['server.cpp'] = text
    return files
