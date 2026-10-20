"""Serialize C-generation resume validation with exec publication.

Apply after generation contexts. The queue insertion is the resume operation's
linearization point; retaining the shared Thread lock through insertion prevents
an old C callback from validating before exec and scheduling after publication.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('generation resume anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


RESUME = '''void DarlingServer::Thread::resume(dtape_thread_t* expectedGeneration) {
    if (resourceCleanupLane(this)) {
        // This permanent kernel Thread is never replaced. Preserve its native
        // allocation-free wake path while still rejecting an invalid context.
        if (!expectedGeneration || _dtapeThread.load() == expectedGeneration)
            preparedResourceCleanupWake();
        return;
    }
    std::shared_lock lock(_rwlock);
    if (expectedGeneration && _dtapeThread.load() != expectedGeneration) return;
    if (!_suspended) return;
    auto owner = _dead ? _selfReference : shared_from_this();
    if (!owner) std::terminate();
    // Worker dequeue drops its queue lock before entering Thread::doWork, so
    // holding this shared lock through enqueue introduces no reverse lock edge.
    Server::sharedInstance().scheduleResumedThread(std::move(owner));
};
'''


def patch_generation_resume(files):
    files = dict(files)
    files['thread.hpp'] = once(files['thread.hpp'], '\t\tvoid resume();',
        '\t\tvoid resume(dtape_thread_t* expectedGeneration = nullptr);')
    source = files['thread.cpp']
    begin = source.index('void DarlingServer::Thread::resume() {')
    end = source.index('void DarlingServer::Thread::terminate()', begin)
    files['thread.cpp'] = source[:begin] + RESUME + '\n' + source[end:]
    files['server.cpp'] = once(files['server.cpp'],
        'DarlingServer::Thread::fromKernelContext(thread_context)->resume();',
        'DarlingServer::Thread::fromKernelContext(thread_context)->resume(\n'
        '            DarlingServer::Thread::kernelHandleFromContext(thread_context));')
    return files
