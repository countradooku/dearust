#include <darlingserver/workers.hpp>
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <new>

static thread_local long failAfter = -1;
static thread_local bool allocationDenied = false;
static std::atomic<long> liveAllocations{0};
void* operator new(size_t size) {
    if (failAfter == 0) { failAfter = -1; allocationDenied = true; throw std::bad_alloc(); }
    if (failAfter > 0) --failAfter;
    if (void* result = std::malloc(size ? size : 1)) { ++liveAllocations; return result; }
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept { if (value) { --liveAllocations; std::free(value); } }
void operator delete(void* value, size_t) noexcept { operator delete(value); }

static std::atomic<unsigned> launchesUntilFailure{0};
bool workerTestLaunchFailure() noexcept {
    auto remaining = launchesUntilFailure.load();
    return remaining && launchesUntilFailure.fetch_sub(1) == 1;
}

static std::atomic<unsigned> activeWorkers{0}, temporaryWaiters{0};
static std::atomic<bool> blockTimeout{false}, timeoutEntered{false}, releaseTimeout{false};
static std::atomic<bool> shutdownEntered{false};
void workerTestEnter() noexcept { ++activeWorkers; }
void workerTestExit() noexcept { --activeWorkers; }
void workerTestWaiting() noexcept { ++temporaryWaiters; }
void workerTestWaitDone() noexcept { --temporaryWaiters; }
void workerTestShutdown() noexcept { shutdownEntered = true; }
void workerTestTimeout() noexcept {
    if (blockTimeout.exchange(false)) {
        timeoutEntered = true;
        while (!releaseTimeout.load()) std::this_thread::yield();
    }
}

struct Item { std::atomic<bool> entered{false}, release{true}; };
using Queue = DarlingServer::WorkQueue<Item*>;
static void execute(Item* item) {
    item->entered = true;
    while (!item->release.load()) std::this_thread::yield();
}
static void wait(const std::atomic<bool>& condition) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!condition.load()) {
        assert(std::chrono::steady_clock::now() < deadline);
        std::this_thread::yield();
    }
}
static void waitForWorkers(unsigned expected) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (activeWorkers.load() != expected) {
        assert(std::chrono::steady_clock::now() < deadline);
        std::this_thread::yield();
    }
}
static void waitForTemporary() {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!temporaryWaiters.load()) {
        assert(std::chrono::steady_clock::now() < deadline);
        std::this_thread::yield();
    }
}
static void warmup() { std::thread worker([] {}); worker.join(); }

int main(int argc, char** argv) {
    warmup();
    if (argc == 3 && std::strcmp(argv[1], "--constructor-nth") == 0) {
        allocationDenied = false;
        failAfter = std::strtol(argv[2], nullptr, 10);
        try { Queue queue(execute); }
        catch (const std::bad_alloc&) { failAfter = -1; return allocationDenied ? 0 : 3; }
        failAfter = -1;
        return 2; // This index did not encounter an allocation.
    }
#ifndef WORKER_TEST_ORIGINAL
    const auto initialLiveAllocations = liveAllocations.load();
    unsigned constructorFailures = 0;
    bool constructorSucceeded = false;
    for (long nth = 0; nth != 64; ++nth) {
        const auto baseline = liveAllocations.load();
        allocationDenied = false;
        failAfter = nth;
        try { Queue queue(execute); failAfter = -1; constructorSucceeded = true; }
        catch (const std::bad_alloc&) { failAfter = -1; assert(allocationDenied); ++constructorFailures; }
        assert(activeWorkers == 0);
        assert(liveAllocations == baseline);
        if (constructorSucceeded) break;
    }
    assert(constructorSucceeded && constructorFailures >= 6);
    for (unsigned nth: {1u, 2u}) {
        const auto baseline = liveAllocations.load();
        launchesUntilFailure = nth;
        bool failed = false;
        try { Queue queue(execute); }
        catch (const std::system_error& error) { failed = error.code().value() == EAGAIN; }
        assert(failed && activeWorkers == 0 && liveAllocations == baseline);
        launchesUntilFailure = 0;
    }
    unsigned growthFailures = 0;
    for (long nth = 0; nth != 8; ++nth) {
        auto queue = std::make_unique<Queue>(execute);
        Item first, second, accepted;
        first.release = false; second.release = false;
        // Queue permanent work without optional growth so both workers become
        // occupied before the ordinary enqueue exercises temporary creation.
        DarlingServer::ReservedWorkNode<Item*> one, two;
        queue->pushReserved(one, &first); wait(first.entered);
        queue->pushReserved(two, &second); wait(second.entered);
        allocationDenied = false;
        failAfter = nth;
        queue->push(&accepted); // Must not throw after accepting this item.
        failAfter = -1;
        if (allocationDenied) ++growthFailures;
        first.release = true; second.release = true;
        wait(accepted.entered);
        queue.reset(); waitForWorkers(0);
    }
    assert(growthFailures >= 2);
    {
        Queue queue(execute);
        Item first, second, accepted;
        first.release = false; second.release = false;
        DarlingServer::ReservedWorkNode<Item*> one, two;
        queue.pushReserved(one, &first); wait(first.entered);
        queue.pushReserved(two, &second); wait(second.entered);
        launchesUntilFailure = 1;
        queue.push(&accepted);
        launchesUntilFailure = 0;
        first.release = true; second.release = true;
        wait(accepted.entered);
    }
    unsigned shutdownRaces = 0;
    for (unsigned round = 0; round != 64; ++round) {
        auto* queue = new Queue(execute);
        Item first, second, third;
        first.release = false; second.release = false;
        DarlingServer::ReservedWorkNode<Item*> one, two;
        queue->pushReserved(one, &first); wait(first.entered);
        queue->pushReserved(two, &second); wait(second.entered);
        blockTimeout = round % 2 != 0;
        timeoutEntered = false; releaseTimeout = false; shutdownEntered = false;
        queue->push(&third); wait(third.entered); waitForTemporary();
        if (round % 2) wait(timeoutEntered);
        std::thread shutdown([&] { delete queue; });
        wait(shutdownEntered);
        releaseTimeout = true;
        first.release = true; second.release = true;
        shutdown.join(); waitForWorkers(0);
        ++shutdownRaces;
    }
    assert(temporaryWaiters == 0);
    const auto allocationDeadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (liveAllocations.load() != initialLiveAllocations) {
        assert(std::chrono::steady_clock::now() < allocationDeadline);
        std::this_thread::yield();
    }
    std::printf("{\"constructor_allocation_failures\":%u,\"constructor_thread_failures\":2,"
        "\"optional_growth_allocation_failures\":%u,\"growth_thread_failure_accepted\":true,"
        "\"timeout_shutdown_races\":%u,\"queue_owned_allocations_restored\":true}\n",
        constructorFailures, growthFailures, shutdownRaces);
#else
    return 4;
#endif
}
