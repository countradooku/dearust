#!/usr/bin/env python3
"""Stage bootstrap Thread Process-map storage without spanning C with native locks.

Apply after registration_lock_scope. The permanent kernel Process needs no
cleanup-lane pin; its private weak-map node and bucket budget precede C creation.
"""


def once(text, old, new):
    if text.count(old) != 1: raise ValueError('bootstrap storage anchor changed: '+old[:100])
    return text.replace(old,new,1)

STAGE = '''void DarlingServer::Thread::stageBootstrapPublication() {
    if (_publicationStaged || _publicationPrepared || _publicationCommitted) return;
    // Local node storage is private, even when the current bootstrap helper is
    // an active microthread. Neither Process lock nor native TLS spans C.
    decltype(_process->_threads) privateMap;
    _publicationIdentity = shared_from_this();
    privateMap.emplace(_nstid, _publicationIdentity);
    auto node = privateMap.extract(_nstid);
    std::unique_lock lock(_process->_rwlock);
    if (_process->_dead || _process->_resourceCleanupQueued)
        throw std::system_error(ESRCH, std::generic_category());
    const auto pending = _process->_preparedThreadsInFlight + 1;
    if (!pending || pending > _process->_threads.max_size() ||
            _process->_threads.size() > _process->_threads.max_size() - pending)
        throw std::overflow_error("Process thread publication capacity exhausted");
    _process->_threads.reserve(_process->_threads.size() + pending);
    ++_process->_preparedThreadsInFlight;
    _publicationNode = std::move(node);
    _publicationStaged = true;
}

'''
FINISH = '''    if (_publicationStaged) {
        if (!_process->_preparedThreadsInFlight) std::terminate();
        --_process->_preparedThreadsInFlight;
        _publicationStaged = false;
        _publicationNode = decltype(_publicationNode){};
    }
'''
ABORT_PRIVATE = '''    if (!_publicationPrepared) {
        if (_publicationStaged) {
            std::unique_lock lock(_process->_rwlock);
            if (!_process->_preparedThreadsInFlight) std::terminate();
            --_process->_preparedThreadsInFlight;
            _publicationStaged = false;
            _publicationNode = decltype(_publicationNode){};
        }
        return;
    }'''


def patch_bootstrap_thread_storage(files):
    files=dict(files)
    files['process.hpp']=once(files['process.hpp'],
        '\t\tstd::unordered_map<uint64_t, std::weak_ptr<Thread>> _threads;',
        '\t\tstd::unordered_map<uint64_t, std::weak_ptr<Thread>> _threads;\n        size_t _preparedThreadsInFlight = 0;')
    files['thread.hpp']=once(files['thread.hpp'], '    bool _publicationPrepared = false;',
        '''    bool _publicationPrepared = false;
    bool _publicationStaged = false;
    std::unordered_map<uint64_t, std::weak_ptr<Thread>>::node_type _publicationNode;
    void stageBootstrapPublication();''')
    source=files['thread.cpp']
    old='''    if (bootstrap) {
        if (currentThread()) throw std::logic_error("active microthread before cleanup bootstrap");
        // No active microthread can suspend here. Preserve startup's preallocated
        // Process publication slot until its cleanup lane exists.
        preparePublication();'''
    new='''    if (bootstrap) {
        // A C bootstrap helper can already be an active microthread. Stage only
        // private storage here; actual publication follows C without allocation.
        stageBootstrapPublication();'''
    source=once(source,old,new)
    source=once(source, 'void DarlingServer::Thread::preparePublication() {', STAGE+'void DarlingServer::Thread::preparePublication() {')
    old='''            // Allocation belongs to preparation. Process readers remain excluded
            // until the global thread Registry has committed all its indexes.
            _process->_threads.emplace(_nstid, _publicationIdentity);'''
    new='''            if (_publicationStaged) {
                auto inserted = _process->_threads.insert(std::move(_publicationNode));
                if (!inserted.inserted) std::terminate();
            } else {
                // Legacy/ordinary insertion may not consume capacity promised
                // to a private bootstrap publication still acquiring C handles.
                const auto pending = _process->_preparedThreadsInFlight + 1;
                if (!pending || pending > _process->_threads.max_size() ||
                        _process->_threads.size() > _process->_threads.max_size() - pending)
                    throw std::overflow_error("Process thread publication capacity exhausted");
                _process->_threads.reserve(_process->_threads.size() + pending);
                _process->_threads.emplace(_nstid, _publicationIdentity);
            }'''
    source=once(source,old,new)
    source=once(source, '''    _publicationCommitted = true;
    _publicationPrepared = false;''', '''    _publicationCommitted = true;
'''+FINISH+'''    _publicationPrepared = false;''')
    source=once(source, '    if (!_publicationPrepared) return;', ABORT_PRIVATE)
    begin=source.index('void DarlingServer::Thread::abortPublication()')
    end=source.index('void DarlingServer::Thread::registerWithProcess()',begin)
    abort=once(source[begin:end], '''    _publicationPrepared = false;
    _publicationPrevious.reset();
    _publicationProcessLock.unlock();''', FINISH+'''    _publicationPrepared = false;
    _publicationPrevious.reset();
    _publicationProcessLock.unlock();''')
    source=source[:begin]+abort+source[end:]
    files['thread.cpp']=source
    # The replacement map is swapped into the Process while private nodes may
    # still exist. Re-budget under the final Process lock, inside the existing
    # fallible preparation region, before any visible generation swap.
    source=files['process.cpp']
    source=once(source, '''        listeners.reserve(_listeningKqchannels.size());''', '''        if (_preparedThreadsInFlight >= replacementThreads.max_size())
            throw std::overflow_error("Process replacement thread capacity exhausted");
        replacementThreads.reserve(_preparedThreadsInFlight + 1);
        listeners.reserve(_listeningKqchannels.size());''')
    files['process.cpp']=source
    return files
