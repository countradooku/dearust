namespace {
static thread_local unsigned checkedRequestedKind = 0, checkedRequestedNth = 0, checkedRequestedOrdinal = 0;
static thread_local unsigned checkedActiveKind = 0, checkedCalls = 0;
static unsigned checkedSeenCalls = 0;
static bool checkedDenied = false;
static pthread_mutex_t checkedOwnedLock = PTHREAD_MUTEX_INITIALIZER;
static void* checkedOwned[256] = {};
static unsigned checkedLive = 0;
static unsigned checkedFailures = 0;
}
extern "C" void dearust_checked_enter(unsigned kind) {
    if (kind != checkedRequestedKind || !kind) return;
    if (checkedRequestedOrdinal > 1) { --checkedRequestedOrdinal; return; }
    actualCleanupRequire(!checkedActiveKind, "nested checked allocation instrumentation");
    checkedActiveKind = kind; checkedCalls = 0; checkedDenied = false;
}
extern "C" void dearust_checked_leave(unsigned kind) {
    if (checkedActiveKind != kind || !kind) return;
    checkedSeenCalls = checkedCalls;
    checkedActiveKind = checkedRequestedKind = 0;
}
bool dearust_checked_allocation_denied() noexcept {
    if (!checkedActiveKind) return false;
    if (++checkedCalls != checkedRequestedNth) return false;
    checkedDenied = true;
    ++checkedFailures;
    return true;
}
void dearust_checked_acquired(void* pointer) noexcept {
    if (!checkedActiveKind || !pointer) return;
    pthread_mutex_lock(&checkedOwnedLock);
    for (auto& slot: checkedOwned) if (!slot) {
        slot = pointer; ++checkedLive; pthread_mutex_unlock(&checkedOwnedLock); return;
    }
    actualCleanupRequire(false, "checked ownership tracking exhausted");
}
void dearust_checked_freed(void* pointer) noexcept {
    if (!pointer) return;
    pthread_mutex_lock(&checkedOwnedLock);
    for (auto& slot: checkedOwned) if (slot == pointer) {
        slot = nullptr; --checkedLive; break;
    }
    pthread_mutex_unlock(&checkedOwnedLock);
}
static unsigned checkedLiveCount() {
    pthread_mutex_lock(&checkedOwnedLock);
    const unsigned count = checkedLive;
    pthread_mutex_unlock(&checkedOwnedLock);
    return count;
}
extern "C" unsigned dearust_checked_port_mode();
extern "C" void dearust_checked_set_port_mode(unsigned);
extern "C" void dearust_checked_mark_donor(void*, int);
extern "C" unsigned long long dearust_checked_task_state(void*, unsigned);

void DarlingServer::Server::_runCheckedIPCAllocationValidation() {
    const auto selfPID = getpid();
    const auto baselineFDs = actualCleanupOpenDescriptors();
    const auto baselineProcesses = processRegistry().size();
    const auto baselineThreads = threadRegistry().size();
    const auto baselineMonitors = _monitors.size();
    const auto baselineEpoll = actualCleanupEpollDescriptors(_epollFD);
    const unsigned savedPortMode = dearust_checked_port_mode();
    dearust_checked_set_port_mode(0);
    auto parent = processRegistry().registerPreparedIfAbsent(selfPID, [&](EternalID eid) {
        return std::make_shared<Process>(selfPID, selfPID, Process::Architecture::x86_64, eid);
    });
    int control[2];
    actualCleanupRequire(pipe2(control, O_CLOEXEC) == 0, "cannot create owned native child control pipe");
    auto childPID = fork();
    actualCleanupRequire(childPID >= 0, "cannot create owned native child");
    if (childPID == 0) {
        close(control[1]);
        char byte;
        while (read(control[0], &byte, 1) < 0 && errno == EINTR) {}
        _Exit(0);
    }
    close(control[0]);
    const auto parentFDs = actualCleanupOpenDescriptors();
    actualCleanupCheckAllocations = true;
    unsigned taskFailures[3][2] = {}, threadFailures[3] = {};
    auto retireProcess = [&](std::shared_ptr<Process>& process) {
        auto monitor = _monitors.back();
        removeMonitor(monitor); monitor.reset();
        actualCleanupRequire(processRegistry().unregisterEntry(process), "cannot retire checked Process");
        std::weak_ptr<Process> weak = process;
        process->abortKernelHandles(); process.reset();
        actualCleanupWait([&]() { return weak.expired() && checkedLiveCount() == 0; });
        _monitorsWaitingToDie.clear();
    };
    for (unsigned mode = 0; mode != 3; ++mode) {
      dearust_checked_set_port_mode(mode);
      for (unsigned donor = 0; donor != 2; ++donor) {
        dearust_checked_mark_donor(parent->_dtapeTask, donor);
        bool completed = false;
        for (unsigned nth = 1; nth <= 64; ++nth) {
            actualCleanupRequire(checkedLiveCount() == 0, "prior checked task retained allocation");
            checkedRequestedKind = 1; checkedRequestedNth = nth;
            std::weak_ptr<Process> weak;
            std::shared_ptr<Process> process;
            bool caught = false;
            try {
                process = processRegistry().registerPreparedIfAbsent(childPID, [&](EternalID eid) {
                    auto value = std::make_shared<Process>(childPID, childPID, Process::Architecture::x86_64, eid);
                    weak = value; return value;
                });
            } catch (const std::bad_alloc&) { caught = true; }
            actualCleanupRequire(!checkedRequestedKind, "checked task C preparation not reached");
            if (checkedDenied) {
                actualCleanupRequire(caught && !process, "checked task allocation failure was accepted");
                actualCleanupWait([&]() { return weak.expired() && checkedLiveCount() == 0; });
                ++taskFailures[mode][donor];
            } else {
                actualCleanupRequire(!caught && process && checkedSeenCalls + 1 == nth,
                    "checked task enumeration did not finish at first successful preparation");
                retireProcess(process);
                completed = true;
            }
            actualCleanupWait([&]() { return actualCleanupOpenDescriptors() == parentFDs; });
            actualCleanupRequire(processRegistry().size() == baselineProcesses + 1 &&
                !processRegistry().lookupEntryByID(childPID) && !processRegistry().lookupEntryByNSID(childPID) &&
                _monitors.size() == baselineMonitors + 1 &&
                actualCleanupEpollDescriptors(_epollFD) == baselineEpoll + 1,
                "checked task failure left public ownership");
            if (completed) break;
        }
        actualCleanupRequire(completed, "checked task allocation enumeration bound exceeded");
    }
      }
    dearust_checked_set_port_mode(0);
    unsigned semaphoreFailures = 0;
    for (unsigned nth = 1; nth != 4; ++nth) {
        checkedRequestedKind = 3; checkedRequestedNth = nth; checkedRequestedOrdinal = 1;
        std::weak_ptr<Process> weak;
        std::shared_ptr<Process> value;
        bool caught = false;
        try {
            value = processRegistry().registerPreparedIfAbsent(childPID, [&](EternalID eid) {
                auto next = std::make_shared<Process>(childPID, childPID, Process::Architecture::x86_64, eid);
                weak = next; return next;
            });
        } catch (const std::bad_alloc&) { caught = true; }
        actualCleanupRequire(!checkedRequestedKind, "Process semaphore boundary not reached");
        if (nth != 3) {
            actualCleanupRequire(checkedDenied && caught && !value, "Process semaphore failure accepted");
            actualCleanupWait([&]() { return weak.expired() && checkedLiveCount() == 0; });
            ++semaphoreFailures;
        } else {
            actualCleanupRequire(!checkedDenied && !caught && value && checkedSeenCalls == 2,
                "Process semaphore success did not cover both allocations");
            retireProcess(value);
        }
    }
    dearust_checked_mark_donor(parent->_dtapeTask, 0);
    auto process = processRegistry().registerPreparedIfAbsent(childPID, [&](EternalID eid) {
        return std::make_shared<Process>(childPID, childPID, Process::Architecture::x86_64, eid);
    });
    unsigned long long taskState[4];
    for (unsigned field = 0; field != 4; ++field)
        taskState[field] = dearust_checked_task_state(process->_dtapeTask, field);
    const auto threadFDs = actualCleanupOpenDescriptors();
    for (unsigned mode = 0; mode != 3; ++mode) {
      dearust_checked_set_port_mode(mode);
      bool completed = false;
      for (unsigned nth = 1; nth <= 64; ++nth) {
        checkedRequestedKind = 2; checkedRequestedNth = nth;
        std::weak_ptr<Thread> weak;
        std::shared_ptr<Thread> thread;
        bool caught = false;
        try {
            thread = threadRegistry().registerPreparedIfAbsent(childPID, [&](EternalID eid) {
                auto value = std::make_shared<Thread>(process, childPID, eid);
                weak = value; return value;
            });
        } catch (const std::bad_alloc&) { caught = true; }
        actualCleanupRequire(!checkedRequestedKind, "checked thread C preparation not reached");
        if (checkedDenied) {
            actualCleanupRequire(caught && !thread, "checked thread allocation failure was accepted");
            actualCleanupWait([&]() { return weak.expired() && checkedLiveCount() == 0; });
            ++threadFailures[mode];
        } else {
            actualCleanupRequire(!caught && thread && checkedSeenCalls + 1 == nth,
                "checked thread enumeration did not finish at first successful preparation");
            actualCleanupRequire(threadRegistry().unregisterEntry(thread), "cannot retire checked Thread");
            thread->abortKernelHandles(); thread.reset();
            actualCleanupWait([&]() {
                std::shared_lock lock(process->_rwlock);
                return weak.expired() && process->_threads.empty() && checkedLiveCount() == 0;
            });
            completed = true;
        }
        actualCleanupRequire(threadRegistry().size() == baselineThreads &&
            !threadRegistry().lookupEntryByID(childPID) && !threadRegistry().lookupEntryByNSID(childPID) &&
            !process->_dead, "checked thread failure changed public lifetime");
        actualCleanupWait([&]() { return actualCleanupOpenDescriptors() == threadFDs; });
        for (unsigned field = 0; field != 4; ++field)
            actualCleanupRequire(taskState[field] == dearust_checked_task_state(process->_dtapeTask, field),
                "checked thread did not restore task reference/list/semaphore counts");
        if (completed) break;
    }
    actualCleanupRequire(completed, "checked thread allocation enumeration bound exceeded");
    }
    dearust_checked_set_port_mode(0);
    for (unsigned ordinal = 1; ordinal != 5; ++ordinal) {
      for (unsigned nth = 1; nth != 4; ++nth) {
        checkedRequestedKind = 3; checkedRequestedNth = nth; checkedRequestedOrdinal = ordinal;
        std::weak_ptr<Thread> weak;
        std::shared_ptr<Thread> value;
        bool caught = false;
        try {
            value = threadRegistry().registerPreparedIfAbsent(childPID, [&](EternalID eid) {
                auto next = std::make_shared<Thread>(process, childPID, eid);
                weak = next; return next;
            });
        } catch (const std::bad_alloc&) { caught = true; }
        actualCleanupRequire(!checkedRequestedKind, "Thread semaphore boundary not reached");
        if (nth != 3) {
            actualCleanupRequire(checkedDenied && caught && !value, "Thread semaphore failure accepted");
            ++semaphoreFailures;
        } else {
            actualCleanupRequire(!checkedDenied && !caught && value && checkedSeenCalls == 2,
                "Thread semaphore success did not cover both allocations");
            actualCleanupRequire(threadRegistry().unregisterEntry(value), "cannot retire semaphore test Thread");
            value->abortKernelHandles(); value.reset();
        }
        actualCleanupWait([&]() {
            std::shared_lock lock(process->_rwlock);
            return weak.expired() && process->_threads.empty() && checkedLiveCount() == 0;
        });
        for (unsigned field = 0; field != 4; ++field)
            actualCleanupRequire(taskState[field] == dearust_checked_task_state(process->_dtapeTask, field),
                "semaphore failure did not restore task ownership");
      }
    }
    retireProcess(process);
    retireProcess(parent);
    dearust_checked_set_port_mode(savedPortMode);
    close(control[1]);
    int childStatus;
    actualCleanupRequire(waitpid(childPID, &childStatus, 0) == childPID && WIFEXITED(childStatus),
        "owned native child did not exit");
    actualCleanupWait([&]() { return actualCleanupOpenDescriptors() == baselineFDs; });
    unsigned totalFailures = semaphoreFailures;
    for (unsigned mode = 0; mode != 3; ++mode)
        totalFailures += taskFailures[mode][0] + taskFailures[mode][1] + threadFailures[mode];
    actualCleanupRequire(actualCleanupAllocationAttempts == 0 && checkedLiveCount() == 0 &&
        checkedFailures == totalFailures,
        "checked acquisition or cleanup accounting failed");
    actualCleanupCheckAllocations = false;
    actualCleanupTaskDisposals = actualCleanupThreadDisposals = 0;
    char result[512];
    int length = snprintf(result, sizeof(result), "DEARUST_CHECKED_IPC_RESULT {"
        "\"task_default_failures\":[%u,%u,%u],\"task_inherited_importance_failures\":[%u,%u,%u],"
        "\"thread_failures\":[%u,%u,%u],\"port_modes\":[\"ordinary\",\"immovable\",\"pinned\"],"
        "\"semaphore_all_positions_failures\":%u,\"tracked_partial_allocations_remaining\":0,\"cleanup_allocation_attempts\":0,"
        "\"all_ordinary_successes_checked\":true}\n",
        taskFailures[0][0], taskFailures[1][0], taskFailures[2][0],
        taskFailures[0][1], taskFailures[1][1], taskFailures[2][1],
        threadFailures[0], threadFailures[1], threadFailures[2], semaphoreFailures);
    write(STDOUT_FILENO, result, length);
    _runActualPreparedCleanupValidation();
}
