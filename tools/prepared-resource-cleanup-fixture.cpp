// Ordinary native ownership/queue fixture. The included implementation is the
// same source inserted into owned candidates; dtape handles and doWork are
// instrumented adapters, so this does not claim XNU inner-OOM or ucontext proof.
#include "prepared-resource-cleanup.hpp"
#include <atomic>
#include <array>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <functional>
#include <memory>
#include <mutex>
#include <new>
#include <shared_mutex>
#include <stdexcept>
#include <system_error>
#include <thread>

static std::atomic<bool> denyAllocation{false};
static std::atomic<unsigned> forbiddenAllocations{0};
extern "C" void* __real_malloc(size_t);
extern "C" void* __wrap_malloc(size_t size) {
    if (denyAllocation.load()) {
        ++forbiddenAllocations;
        return nullptr;
    }
    return __real_malloc(size);
}
void* operator new(size_t size) {
    if (denyAllocation.load()) {
        ++forbiddenAllocations;
        throw std::bad_alloc();
    }
    if (auto* p = __real_malloc(size ? size : 1)) return p;
    throw std::bad_alloc();
}
void* operator new[](size_t size) { return ::operator new(size); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, size_t) noexcept { std::free(p); }
void operator delete[](void* p, size_t) noexcept { std::free(p); }

using dserver_rpc_architecture_t = int;
struct dtape_task_t { void* context; uint64_t savedEID; bool dying = false; };
struct dtape_thread_t { void* context; bool dying = false; };
struct dtape_semaphore_t { dtape_task_t* owner; };
static std::atomic<unsigned> tasks{0}, threads{0}, semaphores{0}, disposedTasks{0}, disposedThreads{0};
static std::atomic<int> failHandleAt{-1};
static std::atomic<bool> delayTaskDisposal{false}, delayThreadDisposal{false};
static std::atomic<dtape_task_t*> pendingTask{nullptr};
static std::atomic<dtape_thread_t*> pendingThread{nullptr};

static bool failHandle() {
    int value = failHandleAt.load();
    if (value < 0) return false;
    if (value == 0) {
        failHandleAt = -1;
        denyAllocation = true; // Exhaustion persists through asynchronous rollback.
        return true;
    }
    --failHandleAt;
    return false;
}

namespace DarlingServer {
using EternalID = uint64_t;
class Thread;
class Process: public std::enable_shared_from_this<Process> {
    friend class Thread;
public:
    uint64_t _eid;
    unsigned _nspid = 42;
    int _architecture = 0;
    dtape_task_t* _dtapeTask = nullptr;
    dtape_semaphore_t* _dtapeForkWaitSemaphore = nullptr;
    std::weak_ptr<Process> _parentProcess;
    std::shared_ptr<Process> _selfReference;
    std::shared_mutex _rwlock;
    PreparedResourceCleanupJob _resourceCleanupJob;
    bool _resourceCleanupQueued = false;
    bool _dead = false;
    explicit Process(EternalID eid): _eid(eid) {}
    ~Process() { assert(!_dtapeTask && !_dtapeForkWaitSemaphore); ++disposedTasks; }
    void _dispose() { _selfReference = nullptr; }
    void prepareKernelHandles();
    void abortKernelHandles() noexcept;
    static void _resourceCleanupRun(void*) noexcept;
};
class Thread: public std::enable_shared_from_this<Thread> {
public:
    struct KernelThreadConstructorTag {};
    std::shared_ptr<Process> _process;
    uint64_t _nstid = 43;
    dtape_thread_t* _dtapeThread = nullptr;
    dtape_semaphore_t* _s2cPerformSempahore = nullptr;
    dtape_semaphore_t* _s2cReplySempahore = nullptr;
    dtape_semaphore_t* _s2cInterruptEnterSemaphore = nullptr;
    dtape_semaphore_t* _s2cInterruptExitSemaphore = nullptr;
    std::shared_ptr<Thread> _selfReference;
    std::shared_mutex _rwlock;
    PreparedResourceCleanupJob _resourceCleanupJob;
    bool _resourceCleanupQueued = false;
    bool _kernel = false;
    std::function<void()> startup;
    Thread(std::shared_ptr<Process> process): _process(std::move(process)) {}
    Thread(KernelThreadConstructorTag, EternalID): _kernel(true) {
        static dtape_thread_t bootstrapThread{nullptr};
        _dtapeThread = &bootstrapThread;
    }
    ~Thread() {
        if (_kernel) return;
        assert(!_dtapeThread && !_s2cPerformSempahore && !_s2cReplySempahore &&
               !_s2cInterruptEnterSemaphore && !_s2cInterruptExitSemaphore);
        ++disposedThreads;
    }
    void _dispose() { _selfReference = nullptr; }
    void setupKernelThread(std::function<void()> fn) { startup = std::move(fn); }
    void doWork() { startup(); }
    void prepareKernelHandles();
    void abortKernelHandles() noexcept;
    static void _resourceCleanupRun(void*) noexcept;
    static void initializeResourceCleanup();
    static void requireResourceCleanupReady();
    static bool resourceCleanupLane(const Thread*) noexcept;
    static bool resourceCleanupLoggingSuppressed() noexcept;
    static void enqueueResourceCleanup(PreparedResourceCleanupJob&) noexcept;
};
struct FixtureRegistry {
    template<class Factory> std::shared_ptr<Thread> registerPrepared(Factory factory, bool) {
        auto result = factory(1);
        result->prepareKernelHandles();
        return result;
    }
};
static FixtureRegistry& threadRegistry() { static FixtureRegistry registry; return registry; }
}

static dtape_task_t* dtape_task_create(dtape_task_t*, unsigned, void* context, int) {
    if (failHandle()) return nullptr;
    auto* task = static_cast<dtape_task_t*>(__real_malloc(sizeof(dtape_task_t)));
    assert(task);
    task->context = context;
    task->savedEID = static_cast<DarlingServer::Process*>(context)->_eid;
    task->dying = false;
    ++tasks;
    return task;
}
static dtape_thread_t* dtape_thread_create(dtape_task_t* task, uint64_t, void* context) {
    assert(task && task->context);
    if (failHandle()) return nullptr;
    auto* thread = static_cast<dtape_thread_t*>(__real_malloc(sizeof(dtape_thread_t)));
    assert(thread);
    thread->context = context;
    thread->dying = false;
    ++threads;
    return thread;
}
static dtape_semaphore_t* dtape_semaphore_create(dtape_task_t* task, int) {
    assert(task && task->context);
    if (failHandle()) return nullptr;
    auto* semaphore = static_cast<dtape_semaphore_t*>(__real_malloc(sizeof(dtape_semaphore_t)));
    assert(semaphore);
    semaphore->owner = task;
    ++semaphores;
    return semaphore;
}
static void assertCleanupContext() {
    assert(DarlingServer::Thread::resourceCleanupLoggingSuppressed());
}
static void dtape_task_dying(dtape_task_t* task) {
    assertCleanupContext();
    task->dying = true;
}
static void dtape_thread_dying(dtape_thread_t* thread) {
    assertCleanupContext();
    thread->dying = true;
}
static void dtape_semaphore_destroy(dtape_semaphore_t* semaphore) {
    assertCleanupContext();
    assert(semaphore && semaphore->owner);
    --semaphores;
    std::free(semaphore);
}
static void finishTask(void* context) noexcept {
    assertCleanupContext();
    auto* task = static_cast<dtape_task_t*>(context);
    auto* owner = static_cast<DarlingServer::Process*>(task->context);
    assert(owner->_eid == task->savedEID);
    owner->_dispose();
    --tasks;
    std::free(task);
}
static void finishThread(void* context) noexcept {
    assertCleanupContext();
    auto* thread = static_cast<dtape_thread_t*>(context);
    static_cast<DarlingServer::Thread*>(thread->context)->_dispose();
    --threads;
    std::free(thread);
}
static void dtape_task_release(dtape_task_t* task) {
    assertCleanupContext();
    assert(task->dying);
    if (delayTaskDisposal.load()) pendingTask = task;
    else finishTask(task);
}
static void dtape_thread_release(dtape_thread_t* thread) {
    assertCleanupContext();
    assert(thread->dying);
    if (delayThreadDisposal.load()) pendingThread = thread;
    else finishThread(thread);
}

#include "prepared-resource-cleanup-thread.inc"
#include "prepared-resource-cleanup-process.inc"

template<class Predicate> static void waitFor(Predicate predicate) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (!predicate()) {
        assert(std::chrono::steady_clock::now() < deadline);
        std::this_thread::yield();
    }
}

int main() {
    using namespace DarlingServer;
    bool rejectedBeforeReady = false;
    try { Thread::requireResourceCleanupReady(); }
    catch (const std::logic_error&) { rejectedBeforeReady = true; }
    assert(rejectedBeforeReady);
    Thread::initializeResourceCleanup();
    Thread::requireResourceCleanupReady();
    unsigned processCases = 0, threadCases = 0;
    for (int fail = 0; fail != 3; ++fail) {
        auto process = std::make_shared<Process>(100 + fail);
        std::weak_ptr<Process> weak = process;
        failHandleAt = fail < 2 ? fail : -1;
        bool failed = false;
        try { process->prepareKernelHandles(); }
        catch (const std::bad_alloc&) { failed = true; }
        assert(failed == (fail < 2));
        if (!failed) assert(process->_dtapeTask->savedEID == process->_eid);
        denyAllocation = true;
        process->abortKernelHandles();
        process->abortKernelHandles();
        process.reset();
        waitFor([&]() { return weak.expired() && tasks == 0 && semaphores == 0 &&
                              disposedTasks == processCases + 1; });
        denyAllocation = false;
        ++processCases;
    }
    auto process = std::make_shared<Process>(200);
    process->prepareKernelHandles();
    assert(tasks == 1 && semaphores == 1);
    unsigned rejectedProcessCases = 0, rejectedThreadCases = 0;
    for (bool missingTask: {false, true}) {
        auto parent = missingTask ? std::make_shared<Process>(300) : process;
        parent->_dead = !missingTask;
        auto child = std::make_shared<Process>(301);
        child->_parentProcess = parent;
        std::weak_ptr<Process> weak = child;
        bool rejected = false;
        try { child->prepareKernelHandles(); }
        catch (const std::system_error& error) { rejected = error.code().value() == ESRCH; }
        assert(rejected);
        denyAllocation = true;
        child->abortKernelHandles();
        child.reset();
        ++rejectedProcessCases;
        waitFor([&]() { return weak.expired() && tasks == 1 && semaphores == 1 &&
                              disposedTasks == processCases + rejectedProcessCases; });
        denyAllocation = false;

        auto rejectedThread = std::make_shared<Thread>(parent);
        std::weak_ptr<Thread> threadWeak = rejectedThread;
        rejected = false;
        try { rejectedThread->prepareKernelHandles(); }
        catch (const std::system_error& error) { rejected = error.code().value() == ESRCH; }
        assert(rejected);
        denyAllocation = true;
        rejectedThread->abortKernelHandles();
        rejectedThread.reset();
        ++rejectedThreadCases;
        waitFor([&]() { return threadWeak.expired() && threads == 0 && semaphores == 1 &&
                              disposedThreads == rejectedThreadCases; });
        denyAllocation = false;
        parent->_dead = false;
        if (missingTask) ++processCases; // The unprepared parent is destroyed below.
    }
    for (int fail = 0; fail != 6; ++fail) {
        auto thread = std::make_shared<Thread>(process);
        std::weak_ptr<Thread> weak = thread;
        failHandleAt = fail < 5 ? fail : -1;
        bool failed = false;
        try { thread->prepareKernelHandles(); }
        catch (const std::bad_alloc&) { failed = true; }
        assert(failed == (fail < 5));
        denyAllocation = true;
        thread->abortKernelHandles();
        thread->abortKernelHandles();
        thread.reset();
        waitFor([&]() { return weak.expired() && threads == 0 && semaphores == 1 &&
                              disposedThreads == threadCases + rejectedThreadCases + 1; });
        denyAllocation = false;
        ++threadCases;
    }

    auto thread = std::make_shared<Thread>(process);
    std::weak_ptr<Thread> threadWeak = thread;
    thread->prepareKernelHandles();
    delayThreadDisposal = true;
    denyAllocation = true;
    thread->abortKernelHandles();
    thread.reset();
    waitFor([&]() { return pendingThread.load() != nullptr; });
    assert(!threadWeak.expired());
    PreparedResourceCleanupJob finalThread;
    finalThread.context = pendingThread.load(); finalThread.run = finishThread;
    Thread::enqueueResourceCleanup(finalThread);
    waitFor([&]() { return threadWeak.expired() && threads == 0 && semaphores == 1 &&
                          disposedThreads == 7 + rejectedThreadCases; });
    std::weak_ptr<Process> processWeak = process;
    delayTaskDisposal = true;
    process->abortKernelHandles();
    process.reset();
    waitFor([&]() { return pendingTask.load() != nullptr; });
    assert(!processWeak.expired());
    PreparedResourceCleanupJob finalTask;
    finalTask.context = pendingTask.load(); finalTask.run = finishTask;
    Thread::enqueueResourceCleanup(finalTask);
    waitFor([&]() { return processWeak.expired() && tasks == 0 && semaphores == 0 &&
                          disposedTasks == processCases + rejectedProcessCases + 1; });
    denyAllocation = false;

    constexpr unsigned producers = 4, perProducer = 1024;
    struct Item { PreparedResourceCleanupJob job; unsigned producer; unsigned sequence; };
    std::array<Item, producers * perProducer> items;
    std::array<unsigned, producers> next{};
    std::atomic<unsigned> done{0}, ready{0};
    std::atomic<bool> go{false};
    std::array<std::thread, producers> submitters;
    struct CallbackState { std::array<unsigned, producers>* next; std::atomic<unsigned>* done; Item* item; };
    std::array<CallbackState, producers * perProducer> callbacks;
    for (unsigned i = 0; i != items.size(); ++i) {
        items[i].producer = i / perProducer;
        items[i].sequence = i % perProducer;
        callbacks[i] = {&next, &done, &items[i]};
        items[i].job.context = &callbacks[i];
        items[i].job.run = [](void* context) noexcept {
            assertCleanupContext();
            auto* state = static_cast<CallbackState*>(context);
            assert((*state->next)[state->item->producer]++ == state->item->sequence);
            ++*state->done;
        };
    }
    for (unsigned producer = 0; producer != producers; ++producer) {
        submitters[producer] = std::thread([&, producer]() {
            ++ready;
            while (!go) std::this_thread::yield();
            for (unsigned i = 0; i != perProducer; ++i)
                Thread::enqueueResourceCleanup(items[producer * perProducer + i].job);
        });
    }
    waitFor([&]() { return ready == producers; });
    denyAllocation = true;
    go = true;
    for (auto& submitter: submitters) submitter.join();
    waitFor([&]() { return done == items.size(); });
    denyAllocation = false;
    assert(forbiddenAllocations == 0);
    assert(disposedTasks == 7 && disposedThreads == 9);
    std::printf("{\"process_cases\":%u,\"thread_cases\":%u,\"delayed_disposals\":2,"
                "\"retired_process_rejections\":%u,\"retired_thread_rejections\":%u,"
                "\"concurrent_jobs\":%u,\"forbidden_allocations\":%u}\n",
                processCases - 1, threadCases, rejectedProcessCases, rejectedThreadCases,
                done.load(), forbiddenAllocations.load());
}
