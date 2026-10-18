// Native allocation-failure coverage for the owned, GPL-licensed Darling header.
#include <atomic>
#include <cassert>
#include <cstdlib>
#include <new>
#include <thread>
#include <chrono>
#include <iostream>
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

struct Entry {
    using ID = int; using NSID = int;
    int host, guest;
    DarlingServer::EternalID eternal = DarlingServer::EternalIDInvalid;
    Entry(int h, int g): host(h), guest(g) {}
    int id() const { return host; }
    int nsid() const { return guest; }
    auto eternalID() const { return eternal; }
    void _setEternalID(DarlingServer::EternalID e) noexcept { eternal = e; }
};
using Registry = DarlingServer::Registry<Entry>;
static void same(Registry& r, const std::shared_ptr<Entry>& p) {
    assert(r.lookupEntryByID(p->id()).value() == p);
    assert(r.lookupEntryByNSID(p->nsid()).value() == p);
    assert(r.lookupEntryByEternalID(p->eternalID()).value() == p);
}
static unsigned failures = 0, successes = 0;
static void transaction(int count, int mode) {
    // Each allocation offset gets a fresh registry so rehash paths are covered.
    for (long offset = 0; offset < 80; ++offset) {
        Registry r;
        std::vector<std::shared_ptr<Entry>> old;
        for (int i = 0; i < count; ++i) {
            auto p = std::make_shared<Entry>(i, 1000+i);
            assert(r.registerEntry(p)); old.push_back(p);
            assert(p->eternalID() == static_cast<unsigned>(i));
        }
        // 0 insert; 1 replace both; 2 host collision; 3 namespace collision;
        // 4 split collision; 5 factory registration (with factory allocation).
        int host = mode == 1 || mode == 2 || mode == 4 ? 0 : 500;
        int guest = mode == 1 || mode == 3 ? 1000 : mode == 4 ? 1001 : 1500;
        auto candidate = std::make_shared<Entry>(host, guest);
        bool failed = false;
        failAfter = offset;
        try {
            if (mode == 5) {
                candidate = r.registerIfAbsent(guest, [&] {
                    return std::make_shared<Entry>(host, guest);
                });
            } else assert(r.registerEntry(candidate, mode != 0));
        } catch (const std::bad_alloc&) { failed = true; }
        failAfter = -1;
        if (failed) {
            ++failures;
            assert(candidate->eternalID() == DarlingServer::EternalIDInvalid);
            assert(r.size() == static_cast<unsigned>(count));
            assert(r.copyEntries().size() == static_cast<unsigned>(count));
            for (auto p : old) same(r, p);
            assert(!r.lookupEntryByEternalID(count));
            if (host == 500) assert(!r.lookupEntryByID(host));
            if (guest == 1500) assert(!r.lookupEntryByNSID(guest));
            // Failure consumes no ID and leaves the registry immediately usable.
            auto next = std::make_shared<Entry>(600, 1600);
            assert(r.registerEntry(next));
            assert(next->eternalID() == static_cast<unsigned>(count)); same(r, next);
        } else {
            ++successes; same(r, candidate);
            assert(candidate->eternalID() == static_cast<unsigned>(count));
            for (auto p : old) {
                assert(r.lookupEntryByEternalID(p->eternalID()).value() == p);
                assert(r.lookupEntryByID(p->id()).value() == (p->id() == host ? candidate : p));
                assert(r.lookupEntryByNSID(p->nsid()).value() == (p->nsid() == guest ? candidate : p));
            }
            break;
        }
    }
}
static void lookupMustLock(Registry& target, const std::function<void()>& lookup) {
    std::atomic<bool> held{false}, released{false};
    std::thread owner([&] {
        target.lock(); held = true;
        std::this_thread::sleep_for(std::chrono::milliseconds(40));
        released = true; target.unlock();
    });
    while (!held) std::this_thread::yield();
    lookup();
    assert(released); owner.join();
}
int main() {
    for (int count : {0, 1, 12, 13, 29, 59}) {
        transaction(count, 0); transaction(count, 5);
        if (count > 0) for (int mode : {1,2,3}) transaction(count, mode);
        if (count > 1) transaction(count, 4);
    }
    Registry r, other;
    auto p = std::make_shared<Entry>(1, 2); assert(r.registerEntry(p));
    try { r.registerIfAbsent(3, [&]() -> std::shared_ptr<Entry> {
        same(r, p); throw std::runtime_error("ordinary factory failure");
    }); assert(false); } catch (const std::runtime_error&) {}
    for (int key = 0; key < 3; ++key) lookupMustLock(r, [&] {
        if (key == 0) assert(r.lookupEntryByID(1));
        if (key == 1) assert(r.lookupEntryByNSID(2));
        if (key == 2) assert(r.lookupEntryByEternalID(0));
    });
    // A different registry of the same Entry type must still take its own lock.
    assert(!r.registerIfAbsent(3, [&]() -> std::shared_ptr<Entry> {
        lookupMustLock(other, [&] { assert(!other.lookupEntryByID(10)); });
        assert(!other.registerIfAbsent(4, [&]() -> std::shared_ptr<Entry> { same(r, p); return nullptr; }));
        same(r, p); return nullptr;
    }));
    assert(r.registerIfAbsent(2, []() -> std::shared_ptr<Entry> { assert(false); return nullptr; }) == p);
    auto duplicate = std::make_shared<Entry>(1, 2);
    assert(!r.registerEntry(duplicate)); same(r,p);
    assert(r.unregisterEntry(p)); assert(r.size() == 0); assert(r.copyEntries().empty());
    std::cout << "injected failures=" << failures << " successful transactions=" << successes
              << "; factory exception, nested bypass, three-index rollback passed\n";
}
