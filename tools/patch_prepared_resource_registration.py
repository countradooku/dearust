#!/usr/bin/env python3
"""Prepared resource registration in owned server copies only.

Reserved identities are consumed before factories and never reused. Legacy APIs
remain for compatibility; actual Process/Thread paths are converted explicitly.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared registration anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_registry_prepared(text):
    text = once(text, '#include <stdexcept>', '#include <stdexcept>\n#include <utility>\n#include <type_traits>')
    anchor = '\tpublic:\n\t\tusing ID = typename Entry::ID;'
    helper = '''
        EternalID reserveIdentityLocked() {
            if (_eternalCounter == EternalIDInvalid)
                throw std::overflow_error("Registry eternal ID exhausted");
            // A failed factory may already have exposed this identity to the
            // kernel. Never recycle it, including on allocation failure.
            return _eternalCounter++;
        }

        void publishPreparedLocked(const std::shared_ptr<Entry>& entry, EternalID eid) {
            static_assert(std::is_integral<typename Entry::ID>::value, "integer host identity required");
            static_assert(std::is_integral<typename Entry::NSID>::value, "integer guest identity required");
            static_assert(noexcept(entry->commitPublication()), "commit must not throw");
            static_assert(noexcept(entry->abortPublication()), "publication rollback must not throw");
            static_assert(noexcept(entry->abortKernelHandles()), "kernel rollback must not throw");
            const auto id = entry->id();
            const auto nsid = entry->nsid();
            bool addedID = false, addedNSID = false, addedEID = false;
            try {
                if (entry->eternalID() != eid)
                    throw std::logic_error("Factory did not preserve reserved eternal identity");
                // Allocate all registry storage before foreign handles and epoll.
                // Null private slots are never observable as registered entries.
                addedID = _map.try_emplace(id, nullptr).second;
                addedNSID = _nsmap.try_emplace(nsid, nullptr).second;
                addedEID = _emap.try_emplace(eid, nullptr).second;
                if (!addedEID) throw std::logic_error("Reserved eternal identity collision");
                entry->prepareKernelHandles();
                // Process epoll ADD is the final fallible external operation;
                // Thread attachment holds the Process lock across this commit.
                entry->preparePublication();
            } catch (...) {
                entry->abortPublication();
                if (addedEID) _emap.erase(eid);
                if (addedNSID) _nsmap.erase(nsid);
                if (addedID) _map.erase(id);
                entry->abortKernelHandles();
                throw;
            }
            // Integer-key lookup and shared_ptr assignment cannot allocate.
            // Old replacement entries remain alive in the historical EID map.
            _map.find(id)->second = entry;
            _nsmap.find(nsid)->second = entry;
            _emap.find(eid)->second = entry;
            entry->commitPublication();
        }

'''
    text = once(text, anchor, helper + anchor)
    public = '''
        template<class Factory>
        std::shared_ptr<Entry> registerPreparedIfAbsent(NSID nsid, Factory&& factory) {
            std::unique_lock lock(_rwlock);
            auto found = _nsmap.find(nsid);
            if (found != _nsmap.end() && found->second) return found->second;
            const auto eid = reserveIdentityLocked();
            FactoryScope scope(this);
            auto entry = std::forward<Factory>(factory)(eid);
            if (!entry) return nullptr;
            publishPreparedLocked(entry, eid);
            return entry;
        }

        template<class Factory>
        std::shared_ptr<Entry> registerPrepared(Factory&& factory, bool replace = false) {
            std::unique_lock lock(_rwlock);
            const auto eid = reserveIdentityLocked();
            FactoryScope scope(this);
            auto entry = std::forward<Factory>(factory)(eid);
            if (!entry) return nullptr;
            if (!replace && (_map.find(entry->id()) != _map.end() || _nsmap.find(entry->nsid()) != _nsmap.end())) {
                entry->abortPublication();
                entry->abortKernelHandles();
                return nullptr;
            }
            publishPreparedLocked(entry, eid);
            return entry;
        }

'''
    text = once(text, '\t\tstd::shared_ptr<Entry> registerIfAbsent(', public + '\t\tstd::shared_ptr<Entry> registerIfAbsent(')
    for name in ('it', 'it2', 'it3'):
        # Only lookup sites: unregister and replacement policies remain unchanged.
        old = f'if ({name} == _' + {'it': 'map', 'it2': 'nsmap', 'it3': 'emap'}[name] + '.end()) {\n\t\t\t\treturn std::nullopt;'
        new = old.replace('.end())', f'.end() || !{name}->second)')
        text = once(text, old, new)
    text = once(text, '\t\t\t\tentries.push_back(entry);', '\t\t\t\tif (entry) entries.push_back(entry);')
    begin = text.index('\t\tbool unregisterEntry(std::shared_ptr<Entry> entry) {')
    end = text.index('\n\t\tstd::optional<std::shared_ptr<Entry>> lookupEntryByID(', begin)
    text = text[:begin] + '''        bool unregisterEntry(std::shared_ptr<Entry> entry) {
            std::unique_lock lock(_rwlock);
            // A replacement may share only one numeric index with this owner.
            // Retire each index only when it still points to this exact object;
            // its historical eternal identity must not retain a dead owner.
            bool removed = false;
            auto host = _map.find(entry->id());
            if (host != _map.end() && host->second == entry) {
                _map.erase(host);
                removed = true;
            }
            auto guest = _nsmap.find(entry->nsid());
            if (guest != _nsmap.end() && guest->second == entry) {
                _nsmap.erase(guest);
                removed = true;
            }
            auto eternal = _emap.find(entry->eternalID());
            if (eternal != _emap.end() && eternal->second == entry) {
                _emap.erase(eternal);
                removed = true;
            }
            return removed;
        };
''' + text[end:]
    return text


def patch_registration(files):
    files = dict(files)
    files['registry.hpp'] = patch_registry_prepared(files['registry.hpp'])
    for name, kind in [('process', 'Process'), ('thread', 'Thread')]:
        header = files[name + '.hpp']
        header = once(header, '\t\tEternalID _eid;', '\t\tEternalID _eid = EternalIDInvalid;')
        if name == 'process':
            header = once(header, 'Architecture architecture, FD pipe = FD());', 'Architecture architecture, EternalID eid, FD pipe = FD());')
        else:
            header = once(header, 'NSID nsid, void* stackHint = nullptr);', 'NSID nsid, EternalID eid, void* stackHint = nullptr);')
        header = once(header, kind + '(Kernel' + kind + 'ConstructorTag tag);', kind + '(Kernel' + kind + 'ConstructorTag tag, EternalID eid);')
        files[name + '.hpp'] = header
    process = files['process.cpp']
    process = once(process, 'Architecture architecture, FD pipe):', 'Architecture architecture, EternalID eid, FD pipe):')
    process = once(process, '\t_nspid(nsid),', '\t_nspid(nsid),\n\t_eid(eid),')
    process = once(process, 'Process::Process(KernelProcessConstructorTag tag):', 'Process::Process(KernelProcessConstructorTag tag, EternalID eid):')
    process = once(process, '\t_nspid(0)\n', '\t_nspid(0),\n\t_eid(eid)\n')
    process = once(process, '''		auto proc = std::make_shared<Process>(KernelProcessConstructorTag());
		processRegistry().registerEntry(proc, true);
		return proc;''', '''        return processRegistry().registerPrepared([](EternalID eid) {
            return std::make_shared<Process>(KernelProcessConstructorTag(), eid);
        }, true);''')
    # Moving the weak thread map cannot allocate and preserves all ownership.
    # Later lifecycle work remains durably held by local threads/self references.
    process = once(process, '\t\t_dead = true;\n\t\tthreads = _threads;', '\t\tthreads.swap(_threads);\n\t\t_dead = true;')
    files['process.cpp'] = process
    thread = files['thread.cpp']
    thread = once(thread, 'NSID nsid, void* stackHint):', 'NSID nsid, EternalID eid, void* stackHint):')
    thread = once(thread, '\t_nstid(nsid),', '\t_nstid(nsid),\n\t_eid(eid),')
    thread = once(thread, 'Thread::Thread(KernelThreadConstructorTag tag):', 'Thread::Thread(KernelThreadConstructorTag tag, EternalID eid):')
    thread = once(thread, '\t_tid(-1),\n', '\t_tid(-1),\n\t_eid(eid),\n')
    thread = once(thread, 'auto thread = std::make_shared<Thread>(KernelThreadConstructorTag());', '''auto thread = threadRegistry().registerPrepared([](EternalID eid) {
            return std::make_shared<Thread>(KernelThreadConstructorTag(), eid);
        }, true);''')
    files['thread.cpp'] = thread
    server = files['server.cpp']
    server = once(server, '''		auto thread = std::make_shared<DarlingServer::Thread>(DarlingServer::Thread::KernelThreadConstructorTag());
		thread->registerWithProcess();
		DarlingServer::threadRegistry().registerEntry(thread, true);''', '''        auto thread = DarlingServer::threadRegistry().registerPrepared([](DarlingServer::EternalID eid) {
            return std::make_shared<DarlingServer::Thread>(DarlingServer::Thread::KernelThreadConstructorTag(), eid);
        }, true);''')
    files['server.cpp'] = server
    call = files['call.cpp']
    call = once(call, 'processRegistry().registerIfAbsent(header->pid, [&]() {', 'processRegistry().registerPreparedIfAbsent(header->pid, [&](EternalID eid) {')
    call = once(call, 'static_cast<Process::Architecture>(header->architecture), std::move(lifetimePipe)', 'static_cast<Process::Architecture>(header->architecture), eid, std::move(lifetimePipe)')
    call = once(call, '\n\t\t\tServer::sharedInstance().monitorProcess(tmp);', '')
    call = once(call, 'threadRegistry().registerIfAbsent(header->tid, [&]() {', 'threadRegistry().registerPreparedIfAbsent(header->tid, [&](EternalID eid) {')
    call = once(call, 'std::make_shared<Thread>(process, header->tid, stackHint)', 'std::make_shared<Thread>(process, header->tid, eid, stackHint)')
    call = once(call, '\n\t\t\ttmp->registerWithProcess();', '')
    files['call.cpp'] = call
    return files
