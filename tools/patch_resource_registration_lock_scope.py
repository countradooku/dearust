#!/usr/bin/env python3
"""Keep native Registry/Process locks outside suspension-capable C acquisition.

Stage nodes and bucket capacity before foreign preparation; consume reserved EIDs
on failure. Only nonyielding publication and index commit hold Registry's writer.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('registration lock scope anchor not unique: '+old[:100])
    return text.replace(old, new, 1)


HELPERS = '''        // Every insertion reserves capacity for all outstanding private nodes.
        size_t _preparedInFlight = 0;
        void reserveInsertionLocked() {
            const auto pending = _preparedInFlight + 1;
            if (pending == 0) throw std::overflow_error("Registry preparation count exhausted");
            if (pending > _map.max_size() || pending > _nsmap.max_size() ||
                    pending > _emap.max_size() ||
                    _map.size() > _map.max_size() - pending ||
                    _nsmap.size() > _nsmap.max_size() - pending ||
                    _emap.size() > _emap.max_size() - pending)
                throw std::overflow_error("Registry index capacity exhausted");
            _map.reserve(_map.size() + pending);
            _nsmap.reserve(_nsmap.size() + pending);
            _emap.reserve(_emap.size() + pending);
        }

        struct PreparedIndexNodes {
            typename decltype(_map)::node_type host;
            typename decltype(_nsmap)::node_type guest;
            typename decltype(_emap)::node_type eternal;
            PreparedIndexNodes(const std::shared_ptr<Entry>& entry, EternalID eid) {
                decltype(_map) hosts;
                decltype(_nsmap) guests;
                decltype(_emap) eternals;
                hosts.emplace(entry->id(), entry);
                guests.emplace(entry->nsid(), entry);
                eternals.emplace(eid, entry);
                host = hosts.extract(entry->id());
                guest = guests.extract(entry->nsid());
                eternal = eternals.extract(eid);
            }
        };

        std::shared_ptr<Entry> publishPrepared(const std::shared_ptr<Entry>& entry,
                EternalID eid, bool replace, bool ifAbsent, typename Entry::NSID requested) {
            static_assert(std::is_integral<typename Entry::ID>::value, "integer host identity required");
            static_assert(std::is_integral<typename Entry::NSID>::value, "integer guest identity required");
            static_assert(noexcept(entry->commitPublication()), "commit must not throw");
            static_assert(noexcept(entry->abortPublication()), "publication rollback must not throw");
            static_assert(noexcept(entry->abortKernelHandles()), "kernel rollback must not throw");
            static_assert(noexcept(entry->finishPreparation()), "preparation release must not throw");
            if (entry->eternalID() != eid)
                throw std::logic_error("Factory did not preserve reserved eternal identity");
            PreparedIndexNodes nodes(entry, eid);
            {
                std::unique_lock lock(_rwlock);
                reserveInsertionLocked();
                ++_preparedInFlight;
            }
            bool counted = true;
            // Keep replacement owners alive until after native locks are gone.
            std::shared_ptr<Entry> previousHost, previousGuest, winner;
            try {
                entry->prepareKernelHandles();
                std::unique_lock lock(_rwlock);
                auto host = _map.find(entry->id());
                auto guest = _nsmap.find(entry->nsid());
                if (ifAbsent) {
                    auto existing = _nsmap.find(requested);
                    if (existing != _nsmap.end()) winner = existing->second;
                }
                if (winner || (!replace && (host != _map.end() || guest != _nsmap.end()))) {
                    --_preparedInFlight;
                    counted = false;
                    lock.unlock();
                    entry->finishPreparation();
                    entry->abortKernelHandles();
                    return winner;
                }
                if (_emap.find(eid) != _emap.end())
                    throw std::logic_error("Reserved eternal identity collision");
                entry->preparePublication();
                bool addedHost = false, addedGuest = false, addedEternal = false;
                if (host != _map.end()) previousHost = host->second;
                if (guest != _nsmap.end()) previousGuest = guest->second;
                try {
                    if (host == _map.end()) addedHost = _map.insert(std::move(nodes.host)).inserted;
                    else host->second = entry;
                    if (guest == _nsmap.end()) addedGuest = _nsmap.insert(std::move(nodes.guest)).inserted;
                    else guest->second = entry;
                    addedEternal = _emap.insert(std::move(nodes.eternal)).inserted;
                    if (!addedEternal) std::terminate();
                    entry->commitPublication();
                } catch (...) {
                    if (addedEternal) _emap.erase(eid);
                    if (addedGuest) _nsmap.erase(entry->nsid());
                    else if (previousGuest) _nsmap.find(entry->nsid())->second = previousGuest;
                    if (addedHost) _map.erase(entry->id());
                    else if (previousHost) _map.find(entry->id())->second = previousHost;
                    entry->abortPublication();
                    throw;
                }
                --_preparedInFlight;
                counted = false;
                lock.unlock();
                entry->finishPreparation();
                return entry;
            } catch (...) {
                entry->abortPublication();
                if (counted) {
                    std::unique_lock lock(_rwlock);
                    --_preparedInFlight;
                }
                entry->finishPreparation();
                entry->abortKernelHandles();
                throw;
            }
        }
'''
PUBLIC = '''        template<class Factory>
        std::shared_ptr<Entry> registerPreparedIfAbsent(NSID nsid, Factory&& factory) {
            EternalID eid;
            {
                std::unique_lock lock(_rwlock);
                auto found = _nsmap.find(nsid);
                if (found != _nsmap.end() && found->second) return found->second;
                eid = reserveIdentityLocked();
            }
            // Prepared factories own no Registry lock and need no TLS bypass.
            auto entry = std::forward<Factory>(factory)(eid);
            if (!entry) return nullptr;
            return publishPrepared(entry, eid, false, true, nsid);
        }

        template<class Factory>
        std::shared_ptr<Entry> registerPrepared(Factory&& factory, bool replace = false) {
            EternalID eid;
            {
                std::unique_lock lock(_rwlock);
                eid = reserveIdentityLocked();
            }
            auto entry = std::forward<Factory>(factory)(eid);
            if (!entry) return nullptr;
            return publishPrepared(entry, eid, replace, false, entry->nsid());
        }

'''

THREAD_HANDLES = '''void DarlingServer::Thread::prepareKernelHandles() {
    if (_dtapeThread) { _selfReference = shared_from_this(); return; }
    const bool kernel = _tid < 0;
    const bool bootstrap = kernel && !preparedResourceCleanupState.load(std::memory_order_acquire);
    if (bootstrap) {
        if (currentThread()) throw std::logic_error("active microthread before cleanup bootstrap");
        // No active microthread can suspend here. Preserve startup's preallocated
        // Process publication slot until its cleanup lane exists.
        preparePublication();
        _selfReference = shared_from_this();
        try {
            _dtapeThread = createOwnedThread(_process->_dtapeTask);
            if (!_dtapeThread) throw std::bad_alloc();
        } catch (...) { abortKernelHandles(); throw; }
        return;
    }
    requireResourceCleanupReady();
    _selfReference = shared_from_this();
    dtape_task_t* task = nullptr;
    try {
        {
            std::shared_lock processLock(_process->_rwlock);
            if (_process->_dead || _process->_resourceCleanupQueued || !_process->_dtapeTask)
                throw std::system_error(ESRCH, std::generic_category());
            task = _process->_dtapeTask.load();
            dtape_task_retain(task);
            _resourcePreparationTask = task;
        }
        int status = 0;
        _dtapeThread = createOwnedThread(task, kernel ? nullptr : &status);
        if (status != 0) {
            _resourcePartialThread = _dtapeThread != nullptr;
            throw std::bad_alloc();
        }
        if (!_dtapeThread) throw std::bad_alloc();
        if (!kernel) {
            _s2cPerformSempahore = dtape_semaphore_create(task, 1);
            if (!_s2cPerformSempahore) throw std::bad_alloc();
            _s2cReplySempahore = dtape_semaphore_create(task, 0);
            if (!_s2cReplySempahore) throw std::bad_alloc();
            _s2cInterruptEnterSemaphore = dtape_semaphore_create(task, 0);
            if (!_s2cInterruptEnterSemaphore) throw std::bad_alloc();
            _s2cInterruptExitSemaphore = dtape_semaphore_create(task, 0);
            if (!_s2cInterruptExitSemaphore) throw std::bad_alloc();
        }
        // The preparation pin is released by finishPreparation on the kernel
        // cleanup lane, including failure before the new C thread retained it.
    } catch (...) {
        abortKernelHandles();
        throw;
    }
}
'''
PROCESS_HANDLES = '''void DarlingServer::Process::prepareKernelHandles() {
    if (_dtapeTask) { _selfReference = shared_from_this(); return; }
    const bool kernel = _pid < 0;
    if (!kernel) Thread::requireResourceCleanupReady();
    _selfReference = shared_from_this();
    try {
        if (auto parent = _parentProcess.lock()) {
            std::shared_lock parentLock(parent->_rwlock);
            if (parent->_dead || parent->_resourceCleanupQueued || !parent->_dtapeTask)
                throw std::system_error(ESRCH, std::generic_category());
            _resourcePreparationParentTask = parent->_dtapeTask.load();
            dtape_task_retain(_resourcePreparationParentTask);
        }
        int status = 0;
        _dtapeTask = createOwnedTask(_resourcePreparationParentTask,
            _architecture, kernel ? nullptr : &status);
        if (status != 0) {
            _resourcePartialTask = _dtapeTask != nullptr;
            throw std::bad_alloc();
        }
        if (!_dtapeTask) throw std::bad_alloc();
        if (dearust_prepared_task_saved_eid(_dtapeTask) != _eid) std::terminate();
        if (kernel) return;
        _dtapeForkWaitSemaphore = dtape_semaphore_create(_dtapeTask, 0);
        if (!_dtapeForkWaitSemaphore) throw std::bad_alloc();
    } catch (...) { abortKernelHandles(); throw; }
}
'''


def preparation_pin_methods(kind, field):
    return f'''void DarlingServer::{kind}::finishPreparation() noexcept {{
    if (!{field}) return;
    // No allocation: the Registry already owns this shared control block.
    // Transfer every potentially-final C release to the ready kernel lane.
    _resourcePreparationOwner = shared_from_this();
    _resourcePreparationReleaseTask = {field};
    {field} = nullptr;
    _resourcePreparationCleanupJob.context = this;
    _resourcePreparationCleanupJob.run = _resourcePreparationCleanupRun;
    Thread::enqueueResourceCleanup(_resourcePreparationCleanupJob);
}}

void DarlingServer::{kind}::_resourcePreparationCleanupRun(void* context) noexcept {{
    auto* raw = static_cast<{kind}*>(context);
    auto self = std::move(raw->_resourcePreparationOwner);
    auto* task = raw->_resourcePreparationReleaseTask;
    raw->_resourcePreparationReleaseTask = nullptr;
    // Keep the embedded job's owner alive through a synchronous C disposer.
    if (task) dtape_task_release(task);
}}
'''


def preparation_pin_declarations(kind, field):
    return f'''private:
    dtape_task_t* {field} = nullptr;
    dtape_task_t* _resourcePreparationReleaseTask = nullptr;
    std::shared_ptr<{kind}> _resourcePreparationOwner;
    PreparedResourceCleanupJob _resourcePreparationCleanupJob;
    static void _resourcePreparationCleanupRun(void* context) noexcept;
public:
    void finishPreparation() noexcept;
    void preparePublication();'''


def patch_registration_lock_scope(files):
    files = dict(files)
    registry = files['registry.hpp']
    begin = registry.index('        void publishPreparedLocked(')
    end = registry.index('\n\tpublic:', begin)
    registry = registry[:begin] + HELPERS + '\n' + registry[end:]
    begin = registry.index('        template<class Factory>\n        std::shared_ptr<Entry> registerPreparedIfAbsent(')
    end = registry.index('\t\tstd::shared_ptr<Entry> registerIfAbsent(', begin)
    registry = registry[:begin] + PUBLIC + registry[end:]
    registry = once(registry, '\t\t\tconst auto eid = _eternalCounter;\n',
        '\t\t\tconst auto eid = _eternalCounter;\n            reserveInsertionLocked();\n')
    files['registry.hpp'] = registry
    files['process.hpp'] = once(files['process.hpp'], '    void preparePublication();',
        preparation_pin_declarations('Process', '_resourcePreparationParentTask'))
    files['thread.hpp'] = once(files['thread.hpp'], '    void preparePublication();',
        preparation_pin_declarations('Thread', '_resourcePreparationTask'))
    source = files['thread.cpp']
    begin = source.index('void DarlingServer::Thread::prepareKernelHandles() {')
    end = source.index('void DarlingServer::Thread::abortKernelHandles()', begin)
    source = source[:begin] + THREAD_HANDLES + '\n' + preparation_pin_methods('Thread', '_resourcePreparationTask') + '\n' + source[end:]
    source = once(source, 'void DarlingServer::Thread::preparePublication() {',
        'extern "C" dtape_task_t* dearust_thread_owning_task(dtape_thread_t*);\n'
        'void DarlingServer::Thread::preparePublication() {')
    source = once(source, '''        if (_process->_dead)
            throw std::system_error(ESRCH, std::generic_category(), "process died before thread publication");''',
        '''        if (_process->_dead || _process->_resourceCleanupQueued ||
                (_dtapeThread && dearust_thread_owning_task(_dtapeThread) != _process->_dtapeTask.load()))
            throw std::system_error(ESRCH, std::generic_category(), "process generation retired before thread publication");''')
    files['thread.cpp'] = source
    source = files['process.cpp']
    begin = source.index('void DarlingServer::Process::prepareKernelHandles() {')
    end = source.index('void DarlingServer::Process::abortKernelHandles()', begin)
    source = source[:begin] + PROCESS_HANDLES + '\n' + preparation_pin_methods('Process', '_resourcePreparationParentTask') + '\n' + source[end:]
    source = once(source, '    if (_pid < 0) return;\n    _preparedPublicationMonitor =',
        '''    if (_pid < 0) return;
    if (_resourcePreparationParentTask) {
        auto parent = _parentProcess.lock();
        if (!parent) throw std::system_error(ESRCH, std::generic_category());
        std::shared_lock parentLock(parent->_rwlock);
        if (parent->_dead || parent->_resourceCleanupQueued ||
                parent->_dtapeTask.load() != _resourcePreparationParentTask)
            throw std::system_error(ESRCH, std::generic_category(), "parent generation retired before process publication");
    }
    _preparedPublicationMonitor =''')
    files['process.cpp'] = source
    return files
