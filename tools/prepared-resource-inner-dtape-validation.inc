// Test-only exact malloc denial inside selected real dtape C entry points.
namespace {
static thread_local unsigned innerRequestedKind = 0;
static thread_local unsigned innerRequestedAllocation = 0;
static thread_local unsigned innerActiveKind = 0;
static thread_local unsigned innerAllocationCount = 0;
static thread_local unsigned innerLiveAllocations = 0;
static thread_local unsigned innerDenialsInCall = 0;
static thread_local void* innerAllocations[128] = {};
static unsigned innerCompletedCases = 0;
static unsigned innerTotalDenials = 0;
}
extern "C" void dearust_inner_enter(unsigned kind) {
    if (kind != innerRequestedKind || !kind) return;
    actualCleanupRequire(!innerActiveKind, "nested inner acquisition instrumentation");
    innerActiveKind = kind;
    innerAllocationCount = innerLiveAllocations = innerDenialsInCall = 0;
    memset(innerAllocations, 0, sizeof(innerAllocations));
}
extern "C" void dearust_inner_leave(unsigned kind) {
    if (innerActiveKind != kind || !kind) return;
    actualCleanupRequire(innerDenialsInCall == 1 && innerLiveAllocations == 0,
        "real inner failure did not unwind all locally acquired allocations");
    innerActiveKind = innerRequestedKind = 0;
    ++innerCompletedCases;
}
bool dearust_inner_allocation_denied() noexcept {
    if (!innerActiveKind) return false;
    if (++innerAllocationCount != innerRequestedAllocation) return false;
    ++innerDenialsInCall;
    ++innerTotalDenials;
    return true;
}
void dearust_inner_malloc_acquired(void* pointer) noexcept {
    if (!innerActiveKind || !pointer) return;
    for (auto& slot: innerAllocations) if (!slot) {
        slot = pointer; ++innerLiveAllocations; return;
    }
    actualCleanupRequire(false, "inner allocation tracking capacity exhausted");
}
void dearust_inner_allocation_freed(void* pointer) noexcept {
    if (!innerActiveKind || !pointer) return;
    for (auto& slot: innerAllocations) if (slot == pointer) {
        slot = nullptr; --innerLiveAllocations; return;
    }
}
extern "C" unsigned long long dearust_inner_task_state(void*, unsigned);

void DarlingServer::Server::_runInnerDtapeAllocationValidation() {
    const auto baselineFDs = actualCleanupOpenDescriptors();
    const auto baselineEpoll = actualCleanupEpollDescriptors(_epollFD);
    const auto baselineProcesses = processRegistry().size();
    const auto baselineThreads = threadRegistry().size();
    const auto baselineMonitors = _monitors.size();
    const auto pid = getpid();
    const auto tid = static_cast<pid_t>(syscall(SYS_gettid));
    actualCleanupCheckAllocations = true;
    const unsigned kinds[] = {1, 1, 3, 3};
    const unsigned allocations[] = {1, 2, 1, 2};
    for (unsigned test = 0; test < 4; ++test) {
        std::weak_ptr<Process> weak;
        const auto disposed = actualCleanupTaskDisposals.load();
        innerRequestedKind = kinds[test];
        innerRequestedAllocation = allocations[test];
        bool caught = false;
        try {
            processRegistry().registerPreparedIfAbsent(pid, [&](EternalID eid) {
                auto process = std::make_shared<Process>(pid, pid, Process::Architecture::x86_64, eid);
                weak = process;
                return process;
            });
        } catch (const std::bad_alloc&) { caught = true; }
        actualCleanupRequire(caught && !innerRequestedKind, "inner Process allocation failure not reached");
        const auto expectedDisposals = disposed + (test >= 2 ? 1 : 0);
        actualCleanupWait([&]() { return weak.expired() &&
            actualCleanupTaskDisposals == expectedDisposals &&
            actualCleanupOpenDescriptors() == baselineFDs; });
        actualCleanupRequire(processRegistry().size() == baselineProcesses &&
            !processRegistry().lookupEntryByID(pid) && !processRegistry().lookupEntryByNSID(pid) &&
            _monitors.size() == baselineMonitors &&
            actualCleanupEpollDescriptors(_epollFD) == baselineEpoll,
            "inner Process failure changed public ownership");
    }
    auto process = processRegistry().registerPreparedIfAbsent(pid, [&](EternalID eid) {
        return std::make_shared<Process>(pid, pid, Process::Architecture::x86_64, eid);
    });
    unsigned long long taskState[4];
    for (unsigned field = 0; field != 4; ++field)
        taskState[field] = dearust_inner_task_state(process->_dtapeTask, field);
    const auto processFDs = actualCleanupOpenDescriptors();
    for (unsigned allocation = 1; allocation <= 2; ++allocation) {
        std::weak_ptr<Thread> weak;
        const auto disposed = actualCleanupThreadDisposals.load();
        innerRequestedKind = 2;
        innerRequestedAllocation = allocation;
        bool caught = false;
        try {
            threadRegistry().registerPreparedIfAbsent(tid, [&](EternalID eid) {
                auto thread = std::make_shared<Thread>(process, tid, eid);
                weak = thread;
                return thread;
            });
        } catch (const std::bad_alloc&) { caught = true; }
        actualCleanupRequire(caught && !innerRequestedKind, "inner Thread allocation failure not reached");
        actualCleanupWait([&]() { return weak.expired(); });
        actualCleanupRequire(actualCleanupThreadDisposals == disposed &&
            threadRegistry().size() == baselineThreads && !threadRegistry().lookupEntryByID(tid) &&
            !threadRegistry().lookupEntryByNSID(tid) && process->_threads.empty() && !process->_dead &&
            actualCleanupOpenDescriptors() == processFDs,
            "inner Thread failure changed public ownership");
        for (unsigned field = 0; field != 4; ++field)
            actualCleanupRequire(taskState[field] == dearust_inner_task_state(process->_dtapeTask, field),
                "failed Thread changed task thread/semaphore/reference counts");
    }
    auto thread = threadRegistry().registerPreparedIfAbsent(tid, [&](EternalID eid) {
        return std::make_shared<Thread>(process, tid, eid);
    });
    actualCleanupRequire(dearust_inner_task_state(process->_dtapeTask, 0) == taskState[0] + 1 &&
        dearust_inner_task_state(process->_dtapeTask, 1) == taskState[1] + 1,
        "successful actual Thread did not acquire task active/list ownership");
    std::atomic<bool> dyingDone{false};
    struct DyingContext { dtape_thread_t* thread; std::atomic<bool>* done; } dying{
        thread->_dtapeThread, &dyingDone};
    PreparedResourceCleanupJob dyingJob;
    dyingJob.context = &dying;
    dyingJob.run = [](void* raw) noexcept {
        auto* state = static_cast<DyingContext*>(raw);
        dtape_thread_dying(state->thread);
        dtape_thread_dying(state->thread);
        state->done->store(true);
    };
    Thread::enqueueResourceCleanup(dyingJob);
    actualCleanupWait([&]() { return dyingDone.load(); });
    actualCleanupRequire(dearust_inner_task_state(process->_dtapeTask, 1) == taskState[1] &&
        dearust_inner_task_state(process->_dtapeTask, 0) == taskState[0] + 1,
        "dying did not retire active ownership exactly once before final C release");
    actualCleanupRequire(threadRegistry().unregisterEntry(thread), "cannot retire inner test Thread");
    std::weak_ptr<Thread> threadWeak = thread;
    thread->abortKernelHandles();
    thread.reset();
    actualCleanupWait([&]() {
        std::shared_lock lock(process->_rwlock);
        return threadWeak.expired() && process->_threads.empty();
    });
    for (unsigned field = 0; field != 4; ++field)
        actualCleanupRequire(taskState[field] == dearust_inner_task_state(process->_dtapeTask, field),
            "successful Thread lifecycle did not restore task ownership counts");
    auto monitor = _monitors.back();
    removeMonitor(monitor);
    monitor.reset();
    actualCleanupRequire(processRegistry().unregisterEntry(process), "cannot retire inner test Process");
    std::weak_ptr<Process> weak = process;
    process->abortKernelHandles();
    process.reset();
    actualCleanupWait([&]() { return weak.expired(); });
    _monitorsWaitingToDie.clear();
    actualCleanupWait([&]() { return actualCleanupOpenDescriptors() == baselineFDs; });
    actualCleanupRequire(actualCleanupAllocationAttempts == 0 && innerCompletedCases == 6 &&
        innerTotalDenials == 6, "inner failure coverage or cleanup allocation invariant failed");
    actualCleanupCheckAllocations = false;
    actualCleanupTaskDisposals = actualCleanupThreadDisposals = 0;
    static constexpr char result[] = "DEARUST_INNER_DTAPE_RESULT {\"real_inner_malloc_failures\":6,"
        "\"task_root_and_vm_failures\":2,\"semaphore_wrapper_and_zone_failures\":2,"
        "\"thread_root_and_turnstile_failures\":2,\"partial_allocations_freed\":true,"
        "\"parent_task_counts_unchanged\":true,\"successful_active_count_restored\":true,"
        "\"repeated_dying_idempotent\":true,\"cleanup_allocation_attempts\":0}\n";
    write(STDOUT_FILENO, result, sizeof(result) - 1);
    _runActualPreparedCleanupValidation();
}
