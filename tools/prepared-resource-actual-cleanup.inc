// Test-only native lifecycle validation. No RPCs or guest messages are sent.
namespace {
static std::atomic<int> actualCleanupThrowAfter{-1};
static std::atomic<unsigned> actualCleanupTaskDisposals{0};
static std::atomic<unsigned> actualCleanupThreadDisposals{0};
static std::atomic<bool> actualCleanupCheckAllocations{false};
static std::atomic<unsigned> actualCleanupAllocationAttempts{0};
static void actualCleanupRequire(bool condition, const char* message) {
    if (!condition) {
        write(STDERR_FILENO, message, strlen(message));
        write(STDERR_FILENO, "\n", 1);
        _Exit(2);
    }
}
template<class Predicate> static void actualCleanupWait(Predicate predicate) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (!predicate()) {
        actualCleanupRequire(std::chrono::steady_clock::now() < deadline, "actual cleanup timed out");
        std::this_thread::yield();
    }
}
static unsigned actualCleanupOpenDescriptors() {
    auto* directory = opendir("/proc/self/fd");
    actualCleanupRequire(directory != nullptr, "cannot list native descriptors");
    unsigned count = 0;
    while (auto* entry = readdir(directory)) if (entry->d_name[0] != '.') ++count;
    closedir(directory);
    return count;
}
static unsigned actualCleanupEpollDescriptors(int epoll) {
    char path[128];
    snprintf(path, sizeof(path), "/proc/self/fdinfo/%d", epoll);
    auto* input = fopen(path, "r");
    actualCleanupRequire(input != nullptr, "cannot inspect owned epoll");
    unsigned count = 0;
    char line[512];
    while (fgets(line, sizeof(line), input)) if (!strncmp(line, "tfd:", 4)) ++count;
    fclose(input);
    return count;
}
}

bool dearust_actual_cleanup_allocation_denied() noexcept {
    if (!actualCleanupCheckAllocations.load(std::memory_order_relaxed) ||
        !DarlingServer::Thread::resourceCleanupLoggingSuppressed()) return false;
    ++actualCleanupAllocationAttempts;
    return true;
}

void dearust_actual_cleanup_acquired() {
    auto value = actualCleanupThrowAfter.load();
    if (value > 0 && actualCleanupThrowAfter.fetch_sub(1) == 1) throw std::bad_alloc();
}
void dearust_actual_cleanup_disposed(bool thread) noexcept {
    auto current = DarlingServer::Thread::currentThread();
    actualCleanupRequire(current && DarlingServer::Thread::resourceCleanupLane(current.get()) &&
                         DarlingServer::Thread::resourceCleanupLoggingSuppressed(),
                         "actual C context was disposed outside cleanup lane");
    if (thread) ++actualCleanupThreadDisposals;
    else ++actualCleanupTaskDisposals;
}

void DarlingServer::Server::_runActualPreparedCleanupValidation() {
    const auto baselineFDs = actualCleanupOpenDescriptors();
    const auto baselineEpoll = actualCleanupEpollDescriptors(_epollFD);
    const auto baselineProcesses = processRegistry().size();
    const auto baselineThreads = threadRegistry().size();
    const auto baselineMonitors = _monitors.size();
    const auto pid = getpid();
    const auto tid = static_cast<pid_t>(syscall(SYS_gettid));
    EternalID lastProcessEID = 0, lastThreadEID = 0;
    unsigned processFailures = 0, threadFailures = 0;
    // Establish that the interposition actually denies each public allocation
    // ABI, including ordinary C++ new, on this same real kernel microthread.
    std::atomic<bool> calibrationDone{false};
    unsigned calibrationAttempts = 0;
    struct Calibration { std::atomic<bool>* done; unsigned* attempts; } calibration{
        &calibrationDone, &calibrationAttempts};
    PreparedResourceCleanupJob calibrationJob;
    calibrationJob.context = &calibration;
    calibrationJob.run = [](void* raw) noexcept {
        auto* state = static_cast<Calibration*>(raw);
        actualCleanupCheckAllocations = true;
        void* (*volatile callMalloc)(size_t) = malloc;
        void* (*volatile callCalloc)(size_t, size_t) = calloc;
        void* (*volatile callRealloc)(void*, size_t) = realloc;
        void* (*volatile callMemalign)(size_t, size_t) = memalign;
        void* (*volatile callAligned)(size_t, size_t) = aligned_alloc;
        void* (*volatile callValloc)(size_t) = valloc;
        void* (*volatile callPvalloc)(size_t) = pvalloc;
        actualCleanupRequire(!callMalloc(16) && !callCalloc(1, 16) &&
            !callRealloc(nullptr, 16) && !callMemalign(16, 16) &&
            !callAligned(16, 16) && !callValloc(16) && !callPvalloc(16),
            "allocation interposition calibration did not deny allocation");
        void* pointer = reinterpret_cast<void*>(1);
        actualCleanupRequire(posix_memalign(&pointer, 16, 16) == ENOMEM &&
            pointer == reinterpret_cast<void*>(1), "posix_memalign calibration failed");
        actualCleanupRequire(::operator new(16, std::nothrow) == nullptr,
            "C++ new calibration did not reach allocation interposition");
        *state->attempts = actualCleanupAllocationAttempts.exchange(0);
        actualCleanupCheckAllocations = false;
        state->done->store(true);
    };
    Thread::enqueueResourceCleanup(calibrationJob);
    actualCleanupWait([&]() { return calibrationDone.load(); });
    actualCleanupRequire(calibrationAttempts >= 9, "allocation ABI calibration incomplete");
    actualCleanupCheckAllocations = true;

    auto checkProcessBaseline = [&]() {
        return processRegistry().size() == baselineProcesses &&
            !processRegistry().lookupEntryByID(pid) &&
            !processRegistry().lookupEntryByNSID(pid) &&
            _monitors.size() == baselineMonitors &&
            actualCleanupEpollDescriptors(_epollFD) == baselineEpoll;
    };
    // Actual task + fork semaphore, then completed monitor preparation. These
    // throws happen after successful foreign acquisitions, not inside XNU malloc.
    for (int stage = 1; stage <= 3; ++stage) {
        std::weak_ptr<Process> weak;
        const auto disposed = actualCleanupTaskDisposals.load();
        actualCleanupThrowAfter = stage;
        bool caught = false;
        try {
            processRegistry().registerPreparedIfAbsent(pid, [&](EternalID eid) {
                actualCleanupRequire(eid > lastProcessEID, "failed Process EID was reused");
                lastProcessEID = eid;
                auto process = std::make_shared<Process>(pid, pid, Process::Architecture::x86_64, eid);
                weak = process;
                return process;
            });
        } catch (const std::bad_alloc&) { caught = true; }
        actualCleanupThrowAfter = -1;
        actualCleanupRequire(caught, "actual Process failure injection did not fire");
        actualCleanupWait([&]() { return weak.expired() &&
            actualCleanupTaskDisposals == disposed + 1; });
        actualCleanupRequire(checkProcessBaseline(), "actual Process rollback left public state");
        // No epoll_wait has run in this native test. Retired addresses may now
        // be reclaimed without an already-returned event retaining their pointer.
        _monitorsWaitingToDie.clear();
        actualCleanupWait([&]() { return actualCleanupOpenDescriptors() == baselineFDs; });
        ++processFailures;
    }

    auto process = processRegistry().registerPreparedIfAbsent(pid, [&](EternalID eid) {
        actualCleanupRequire(eid > lastProcessEID, "successful Process reused failed EID");
        lastProcessEID = eid;
        return std::make_shared<Process>(pid, pid, Process::Architecture::x86_64, eid);
    });
    actualCleanupRequire(process && process->_dtapeTask &&
        dearust_prepared_task_saved_eid(process->_dtapeTask) == process->eternalID(),
        "actual task did not save reserved EID");
    actualCleanupRequire(_monitors.size() == baselineMonitors + 1 &&
        actualCleanupEpollDescriptors(_epollFD) == baselineEpoll + 1,
        "successful Process did not publish owned monitor");
    const auto processFDs = actualCleanupOpenDescriptors();
    for (int stage = 1; stage <= 6; ++stage) {
        std::weak_ptr<Thread> weak;
        const auto disposed = actualCleanupThreadDisposals.load();
        actualCleanupThrowAfter = stage;
        bool caught = false;
        try {
            threadRegistry().registerPreparedIfAbsent(tid, [&](EternalID eid) {
                actualCleanupRequire(eid > lastThreadEID, "failed Thread EID was reused");
                lastThreadEID = eid;
                auto thread = std::make_shared<Thread>(process, tid, eid);
                weak = thread;
                return thread;
            });
        } catch (const std::bad_alloc&) { caught = true; }
        actualCleanupThrowAfter = -1;
        actualCleanupRequire(caught, "actual Thread failure injection did not fire");
        actualCleanupWait([&]() { return weak.expired() &&
            actualCleanupThreadDisposals == disposed + 1; });
        actualCleanupRequire(threadRegistry().size() == baselineThreads &&
            !threadRegistry().lookupEntryByID(tid) && !threadRegistry().lookupEntryByNSID(tid),
            "actual Thread rollback left global indexes");
        {
            std::shared_lock lock(process->_rwlock);
            actualCleanupRequire(process->_threads.empty() && !process->_dead,
                "actual Thread rollback left attachment or simulated guest death");
        }
        actualCleanupWait([&]() { return actualCleanupOpenDescriptors() == processFDs; });
        ++threadFailures;
    }
    auto thread = threadRegistry().registerPreparedIfAbsent(tid, [&](EternalID eid) {
        actualCleanupRequire(eid > lastThreadEID, "successful Thread reused failed EID");
        return std::make_shared<Thread>(process, tid, eid);
    });
    actualCleanupRequire(thread && process->_threads.size() == 1 &&
        threadRegistry().lookupEntryByEternalID(thread->eternalID()).value() == thread,
        "successful actual Thread publication missing");
    std::weak_ptr<Thread> weakThread = thread;
    actualCleanupRequire(threadRegistry().unregisterEntry(thread), "cannot retire test Thread");
    thread->abortKernelHandles();
    thread.reset();
    actualCleanupWait([&]() {
        std::shared_lock lock(process->_rwlock);
        return weakThread.expired() && process->_threads.empty() &&
            actualCleanupThreadDisposals == threadFailures + 1;
    });
    actualCleanupRequire(!process->_dead, "test Thread retirement simulated Process death");
    auto monitor = _monitors.back();
    removeMonitor(monitor);
    monitor.reset();
    actualCleanupRequire(processRegistry().unregisterEntry(process), "cannot retire test Process");
    std::weak_ptr<Process> weakProcess = process;
    process->abortKernelHandles();
    process.reset();
    actualCleanupWait([&]() { return weakProcess.expired() &&
        actualCleanupTaskDisposals == processFailures + 1; });
    _monitorsWaitingToDie.clear();
    actualCleanupRequire(checkProcessBaseline(), "successful lifecycle left Process public state");
    actualCleanupWait([&]() { return actualCleanupOpenDescriptors() == baselineFDs; });
    actualCleanupCheckAllocations = false;
    actualCleanupRequire(actualCleanupAllocationAttempts == 0,
        "actual cleanup attempted an interposed allocation under denial");
    char result[512];
    const auto length = snprintf(result, sizeof(result),
        "DEARUST_ACTUAL_CLEANUP_RESULT {\"process_rollback_stages\":%u,"
        "\"thread_rollback_stages\":%u,\"task_context_disposals\":%u,"
        "\"thread_context_disposals\":%u,\"ordinary_successful_lifecycles\":1,"
        "\"descriptor_and_epoll_restored\":true,\"saved_reserved_eid_checked\":true,"
        "\"interposed_cleanup_allocations_denied\":true,\"cleanup_allocation_attempts\":%u,"
        "\"allocation_interposition_calibration_attempts\":%u}\n",
        processFailures, threadFailures, actualCleanupTaskDisposals.load(),
        actualCleanupThreadDisposals.load(), actualCleanupAllocationAttempts.load(), calibrationAttempts);
    actualCleanupRequire(length > 0 && length < static_cast<int>(sizeof(result)), "result overflow");
    write(STDOUT_FILENO, result, static_cast<size_t>(length));
    _Exit(0);
}
