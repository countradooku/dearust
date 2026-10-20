// Ordinary native tests of the actual generated Registry template. Lifecycle
// handles below are a model: no real dtape, monitor, thread, RPC or guest runs.
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <new>
#include <thread>
#include <darlingserver/registry.hpp>

static thread_local long failAfter = -1;
void* operator new(std::size_t size) {
    if (failAfter == 0) throw std::bad_alloc();
    if (failAfter > 0) --failAfter;
    if (void* p = std::malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete[](void* p) noexcept { ::operator delete(p); }
void operator delete[](void* p, std::size_t) noexcept { ::operator delete(p); }

struct HookFailure {};
struct Entry;
using Registry = DarlingServer::Registry<Entry>;
struct Entry {
    using ID = int;
    using NSID = int;
    int host, guest;
    DarlingServer::EternalID eternal;
    bool handle = false, monitor = false, slot = false, prepared = false, committed = false;
    Registry* registry = nullptr;
    std::shared_ptr<Entry> previousID, previousNS;
    static int failHook, handles, monitors, slots, commits, kernelAborts, publicationAborts;
    Entry(int h, int g, DarlingServer::EternalID e): host(h), guest(g), eternal(e) {}
    ~Entry() { assert(!handle && !monitor && !slot && !prepared); }
    int id() const { return host; }
    int nsid() const { return guest; }
    auto eternalID() const { return eternal; }
    void _setEternalID(DarlingServer::EternalID e) noexcept { eternal = e; }
    void observe(bool visible) const;
    static void failAt(int step) {
        if (failHook == step) {
            // Rollback must remain usable with every allocation prohibited.
            failAfter = 0;
            throw HookFailure{};
        }
    }
    void prepareKernelHandles() {
        assert(eternal != DarlingServer::EternalIDInvalid);
        observe(false);
        handle = true; ++handles; failAt(1);
        monitor = true; ++monitors; failAt(2);
        slot = true; ++slots; failAt(3);
    }
    void abortKernelHandles() noexcept {
        assert(!committed);
        ++kernelAborts;
        if (slot) { slot = false; --slots; }
        if (monitor) { monitor = false; --monitors; }
        if (handle) { handle = false; --handles; }
    }
    void preparePublication() {
        assert(handle && monitor && slot && !committed);
        observe(false);
        prepared = true;
        failAt(4);
        // No allocation may follow the final fallible preparation hook.
        failAfter = 0;
    }
    void commitPublication() noexcept {
        assert(prepared && handle && monitor && slot);
        const auto saved = failAfter;
        failAfter = 0;
        observe(true);
        committed = true; prepared = false; ++commits;
        failAfter = saved;
    }
    void abortPublication() noexcept {
        assert(!committed);
        ++publicationAborts;
        prepared = false;
    }
    void releaseModel() noexcept {
        // Test teardown of successful model resources, never a runtime claim.
        committed = false;
        prepared = false;
        if (slot) { slot = false; --slots; }
        if (monitor) { monitor = false; --monitors; }
        if (handle) { handle = false; --handles; }
    }
};
int Entry::failHook = 0;
int Entry::handles = 0;
int Entry::monitors = 0;
int Entry::slots = 0;
int Entry::commits = 0;
int Entry::kernelAborts = 0;
int Entry::publicationAborts = 0;

void Entry::observe(bool visible) const {
    if (!registry) return;
    const auto hostEntry = registry->lookupEntryByID(host);
    const auto guestEntry = registry->lookupEntryByNSID(guest);
    const auto eternalEntry = registry->lookupEntryByEternalID(eternal);
    if (visible) {
        assert(hostEntry && hostEntry.value().get() == this);
        assert(guestEntry && guestEntry.value().get() == this);
        assert(eternalEntry && eternalEntry.value().get() == this);
    } else {
        assert(previousID ? hostEntry && hostEntry.value() == previousID : !hostEntry);
        assert(previousNS ? guestEntry && guestEntry.value() == previousNS : !guestEntry);
        assert(!eternalEntry);
    }
}
static void same(Registry& registry, const std::shared_ptr<Entry>& entry) {
    assert(registry.lookupEntryByID(entry->id()).value() == entry);
    assert(registry.lookupEntryByNSID(entry->nsid()).value() == entry);
    assert(registry.lookupEntryByEternalID(entry->eternalID()).value() == entry);
}
static void clean() { assert(Entry::handles == 0 && Entry::monitors == 0 && Entry::slots == 0); }
static unsigned failures = 0, successes = 0;

static void sweep(int count, int mode) {
    // Cover new nodes and bucket growth in all three actual unordered_maps.
    for (long offset = 0; offset < 100; ++offset) {
        Registry registry;
        std::vector<std::shared_ptr<Entry>> old;
        for (int i = 0; i < count; ++i) {
            auto entry = std::make_shared<Entry>(i, 1000+i, DarlingServer::EternalIDInvalid);
            assert(registry.registerEntry(entry)); old.push_back(entry);
        }
        const int host = mode == 1 || mode == 2 || mode == 4 ? 0 : 500;
        const int guest = mode == 1 || mode == 3 ? 1000 : mode == 4 ? 1001 : 1500;
        std::shared_ptr<Entry> candidate;
        DarlingServer::EternalID reserved = DarlingServer::EternalIDInvalid;
        bool failed = false;
        failAfter = offset;
        try {
            const auto factory = [&](DarlingServer::EternalID eid) {
                reserved = eid;
                candidate = std::make_shared<Entry>(host, guest, eid);
                candidate->registry = &registry;
                if (mode == 1 || mode == 2 || mode == 4) candidate->previousID = old[0];
                if (mode == 1 || mode == 3) candidate->previousNS = old[0];
                if (mode == 4) candidate->previousNS = old[1];
                return candidate;
            };
            auto result = mode == 5 ? registry.registerPreparedIfAbsent(guest, factory)
                                    : registry.registerPrepared(factory, mode != 0);
            assert(result == candidate && result);
        } catch (const std::bad_alloc&) { failed = true; }
        failAfter = -1;
        assert(reserved == static_cast<unsigned>(count));
        if (failed) {
            ++failures; clean();
            assert(registry.size() == static_cast<unsigned>(count));
            assert(registry.copyEntries().size() == static_cast<unsigned>(count));
            for (const auto& entry : old) same(registry, entry);
            assert(!registry.lookupEntryByEternalID(reserved));
            auto next = registry.registerPrepared([&](DarlingServer::EternalID eid) {
                assert(eid == reserved + 1);
                return std::make_shared<Entry>(600, 1600, eid);
            });
            failAfter = -1;
            same(registry, next); next->releaseModel();
        } else {
            ++successes; same(registry, candidate);
            assert(Entry::handles == 1 && Entry::monitors == 1 && Entry::slots == 1);
            for (const auto& entry : old) {
                assert(registry.lookupEntryByEternalID(entry->eternalID()).value() == entry);
                assert(registry.lookupEntryByID(entry->id()).value() ==
                       (entry->id() == host ? candidate : entry));
                assert(registry.lookupEntryByNSID(entry->nsid()).value() ==
                       (entry->nsid() == guest ? candidate : entry));
            }
            candidate->releaseModel(); clean();
            break;
        }
    }
}

static void hookFailures() {
    for (int step = 1; step <= 4; ++step) {
        Registry registry;
        auto old = registry.registerPrepared([](DarlingServer::EternalID eid) {
            return std::make_shared<Entry>(1, 2, eid);
        });
        failAfter = -1;
        const auto oldEID = old->eternalID();
        const auto commits = Entry::commits;
        std::shared_ptr<Entry> candidate;
        Entry::failHook = step;
        try {
            registry.registerPrepared([&](DarlingServer::EternalID eid) {
                candidate = std::make_shared<Entry>(1, 2, eid);
                candidate->registry = &registry;
                candidate->previousID = candidate->previousNS = old;
                return candidate;
            }, true);
            assert(false);
        } catch (const HookFailure&) {}
        assert(failAfter == 0);
        failAfter = -1; Entry::failHook = 0;
        assert(!candidate->handle && !candidate->monitor && !candidate->slot && !candidate->prepared);
        assert(Entry::commits == commits);
        assert(registry.size() == 1 && registry.copyEntries().size() == 1);
        same(registry, old);
        assert(!registry.lookupEntryByEternalID(candidate->eternalID()));
        auto next = registry.registerPrepared([&](DarlingServer::EternalID eid) {
            assert(eid == oldEID + 2);
            return std::make_shared<Entry>(3, 4, eid);
        });
        failAfter = -1;
        same(registry, next); next->releaseModel(); old->releaseModel(); clean();
    }
}

static void identityAndFactoryFailures() {
    Registry registry;
    DarlingServer::EternalID first = DarlingServer::EternalIDInvalid;
    try {
        registry.registerPrepared([&](DarlingServer::EternalID eid) -> std::shared_ptr<Entry> {
            first = eid; failAfter = 0; throw HookFailure{};
        });
        assert(false);
    } catch (const HookFailure&) {}
    failAfter = -1;
    assert(registry.size() == 0);
    assert(!registry.registerPrepared([&](DarlingServer::EternalID eid) -> std::shared_ptr<Entry> {
        assert(eid == first + 1); return nullptr;
    }));
    auto entry = registry.registerPreparedIfAbsent(2, [&](DarlingServer::EternalID eid) {
        assert(eid == first + 2); return std::make_shared<Entry>(1, 2, eid);
    });
    failAfter = -1;
    assert(registry.registerPreparedIfAbsent(2, [](DarlingServer::EternalID) -> std::shared_ptr<Entry> {
        assert(false); return nullptr;
    }) == entry);
    same(registry, entry);
    auto duplicate = registry.registerPrepared([](DarlingServer::EternalID eid) {
        return std::make_shared<Entry>(1, 2, eid);
    });
    assert(!duplicate); same(registry, entry);
    entry->releaseModel(); clean();
}

static void lookupMustLock(Registry& registry, int index = 0) {
    std::atomic<bool> held{false}, released{false};
    std::thread owner([&] {
        registry.lock(); held = true;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        released = true; registry.unlock();
    });
    while (!held) std::this_thread::yield();
    if (index == 0) assert(!registry.lookupEntryByID(91));
    if (index == 1) assert(!registry.lookupEntryByNSID(91));
    if (index == 2) assert(!registry.lookupEntryByEternalID(91));
    assert(released); owner.join();
}

static void replacementRetirement() {
    for (int mode = 0; mode < 4; ++mode) {
        Registry registry;
        auto first = std::make_shared<Entry>(1, 2, DarlingServer::EternalIDInvalid);
        auto second = std::make_shared<Entry>(3, 4, DarlingServer::EternalIDInvalid);
        assert(registry.registerEntry(first));
        assert(registry.registerEntry(second));
        std::weak_ptr<Entry> firstLifetime = first, secondLifetime = second;
        const auto firstEID = first->eternalID(), secondEID = second->eternalID();
        // Replace both indexes, host only, namespace only, or split owners.
        const int host = mode == 2 ? 5 : 1;
        const int guest = mode == 1 ? 6 : mode == 3 ? 4 : 2;
        auto replacement = registry.registerPrepared([&](DarlingServer::EternalID eid) {
            return std::make_shared<Entry>(host, guest, eid);
        }, true);
        failAfter = 0;
        assert(registry.unregisterEntry(first));
        assert(!registry.unregisterEntry(first));
        assert(!registry.lookupEntryByEternalID(firstEID));
        if (host == 1) assert(registry.lookupEntryByID(1).value() == replacement);
        else assert(!registry.lookupEntryByID(1));
        if (guest == 2) assert(registry.lookupEntryByNSID(2).value() == replacement);
        else assert(!registry.lookupEntryByNSID(2));
        first.reset();
        assert(firstLifetime.expired());
        assert(registry.unregisterEntry(second));
        assert(!registry.lookupEntryByEternalID(secondEID));
        second.reset();
        assert(secondLifetime.expired());
        same(registry, replacement);
        failAfter = -1;
        assert(registry.copyEntries().size() == 1);
        failAfter = 0;
        replacement->releaseModel();
        assert(registry.unregisterEntry(replacement));
        failAfter = -1;
        clean();
    }
}

int main() {
    for (int count : {0, 1, 12, 13, 29, 59}) {
        sweep(count, 0); sweep(count, 5);
        if (count > 0) for (int mode : {1, 2, 3}) sweep(count, mode);
        if (count > 1) sweep(count, 4);
    }
    hookFailures(); identityAndFactoryFailures(); replacementRetirement();
    Registry registry, other;
    auto entry = registry.registerPrepared([&](DarlingServer::EternalID eid) {
        assert(!registry.lookupEntryByEternalID(eid));
        lookupMustLock(other);
        return std::make_shared<Entry>(1, 2, eid);
    });
    failAfter = -1;
    for (int index = 0; index < 3; ++index) lookupMustLock(registry, index);
    entry->releaseModel();
    assert(registry.unregisterEntry(entry));
    assert(registry.size() == 0 && registry.copyEntries().empty());
    clean();
    assert(failures > 0 && successes > 0 && Entry::kernelAborts > 0 && Entry::publicationAborts > 0);
    std::cout << "prepared registry: injected allocation failures=" << failures
              << " successful transactions=" << successes
              << "; reserved IDs, hidden staging, coherent commit, four hook failures, "
                 "allocation-free modeled rollback and replacement preservation passed\n";
}
