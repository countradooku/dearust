#include <darlingserver/workers.hpp>
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <new>
#include <vector>

static std::atomic<bool> deny{false};
static std::atomic<unsigned> allocations{0};
void* operator new(size_t size) {
    if (deny.load()) { ++allocations; throw std::bad_alloc(); }
    if (void* value = std::malloc(size ? size : 1)) return value;
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept { std::free(value); }
void operator delete(void* value, size_t) noexcept { std::free(value); }

static std::atomic<bool> destructorStarted{false};
void reservedTestDying() noexcept { destructorStarted = true; }

struct Item {
    DarlingServer::ReservedWorkNode<std::shared_ptr<Item>> work;
    std::atomic<unsigned> calls{0};
    std::atomic<unsigned> inFlight{0};
    std::atomic<bool> entered{false};
    std::atomic<bool> release{true};
};
using Queue = DarlingServer::WorkQueue<std::shared_ptr<Item>>;
static void wait(const std::atomic<bool>& value) {
    while (!value.load()) std::this_thread::yield();
}
static void execute(std::shared_ptr<Item> item) {
    assert(item->inFlight.fetch_add(1) == 0);
    ++item->calls;
    item->entered = true;
    wait(item->release);
    assert(item->inFlight.fetch_sub(1) == 1);
}

int main() {
    {
        Queue queue(execute);
        auto held = std::make_shared<Item>();
        auto queued = std::make_shared<Item>();
        held->release = false;
        deny = true;
        queue.pushReserved(held->work, held);
        wait(held->entered);
        for (int index = 0; index < 1000; ++index) {
            queue.pushReserved(queued->work, queued);
            queue.pushReserved(held->work, held);
        }
        held->release = true;
        while (held->calls.load() < 2 || queued->calls.load() < 1)
            std::this_thread::yield();
        deny = false;
        // A running request produces one subsequent pass. Queued requests
        // coalesce into the pass that has not started yet.
        assert(held->calls == 2 && queued->calls == 1);
    }
    {
        constexpr unsigned count = 4096;
        Queue queue(execute);
        std::vector<std::shared_ptr<Item>> items;
        for (unsigned index = 0; index < count; ++index)
            items.push_back(std::make_shared<Item>());
        std::atomic<bool> start{false};
        std::atomic<unsigned> finished{0};
        std::vector<std::thread> producers;
        for (unsigned producer = 0; producer < 4; ++producer) {
            producers.emplace_back([&, producer]() {
                wait(start);
                for (unsigned index = producer; index < count; index += 4) {
                    queue.pushReserved(items[index]->work, items[index]);
                    queue.pushReserved(items[index]->work, items[index]);
                }
                ++finished;
            });
        }
        deny = true;
        start = true;
        while (finished != 4) std::this_thread::yield();
        for (const auto& item : items) {
            while (item->calls.load() == 0) std::this_thread::yield();
        }
        deny = false;
        for (auto& producer : producers) producer.join();
        for (const auto& item : items) assert(item->calls >= 1 && item->calls <= 2);
    }
    {
        // The queue must release pending embedded ownership even if shutdown
        // occurs before the permanent worker reaches the queued item.
        auto queue = std::make_unique<Queue>(execute);
        auto held = std::make_shared<Item>();
        auto dropped = std::make_shared<Item>();
        std::weak_ptr<Item> weak = dropped;
        held->release = false;
        queue->pushReserved(held->work, held);
        wait(held->entered);
        queue->pushReserved(dropped->work, dropped);
        dropped.reset();
        destructorStarted = false;
        std::thread release([&]() {
            wait(destructorStarted);
            held->release = true;
        });
        deny = true;
        queue.reset();
        deny = false;
        release.join();
        assert(weak.expired());
    }
    assert(allocations == 0);
    std::puts("reserved dying work: queued/inflight coalescing, serialized rerun, 4096 items/four producers, pending shutdown release; zero denied allocations");
}
