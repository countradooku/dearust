// Test-only code appended after the exact ResourceState definition in process.cpp.
namespace {
static thread_local bool actualStateRequestControlFailure = false;
static thread_local bool actualStateFailNextAllocation = false;
static thread_local bool actualStateNativeDenial = false;
static std::atomic<unsigned> actualStateControlFailures{0}, actualStateNativeAttempts{0}, actualStateDisposals{0};
static void actualStateRequire(bool condition, const char* message) {
    if (!condition) {
        write(STDERR_FILENO, message, strlen(message)); write(STDERR_FILENO, "\n", 1); _Exit(2);
    }
}
template<class Predicate> static void actualStateWait(Predicate predicate) {
    const auto stop = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (!predicate()) {
        actualStateRequire(std::chrono::steady_clock::now() < stop, "actual state cleanup timed out");
        std::this_thread::yield();
    }
}
}
void dearust_actual_state_after_constructor() noexcept {
    if (actualStateRequestControlFailure) {
        actualStateRequestControlFailure = false;
        actualStateFailNextAllocation = true;
    }
}
bool dearust_actual_state_allocation_denied() noexcept {
    if (actualStateFailNextAllocation) {
        actualStateFailNextAllocation = false;
        ++actualStateControlFailures;
        return true;
    }
    if (actualStateNativeDenial) { ++actualStateNativeAttempts; return true; }
    return false;
}
void dearust_actual_state_disposed() noexcept {
    auto current = DarlingServer::Thread::currentThread();
    actualStateRequire(current && DarlingServer::Thread::resourceCleanupLane(current.get()) &&
        DarlingServer::Thread::resourceCleanupLoggingSuppressed(), "ResourceState destroyed outside real kernel cleanup lane");
    ++actualStateDisposals;
}
void dearust_actual_state_set_lane_denial(bool) noexcept;
unsigned dearust_actual_state_lane_attempts() noexcept;
extern "C" unsigned long long dearust_actual_state_gate_count(void*);

void DarlingServer::Process::runActualResourceStateValidation() {
    using namespace std::chrono;
    const auto pid = getpid();
    int descriptors[2];
    actualStateRequire(pipe2(descriptors, O_CLOEXEC) == 0, "actual state owned pipe failed");
    FD writeEnd(descriptors[1]);
    FD readEnd(descriptors[0]);
    std::shared_ptr<Process> process;
    std::shared_ptr<ResourceState> state;
    Thread::kernelSync([&]() {
        process = processRegistry().registerPreparedIfAbsent(pid, [&](EternalID eid) {
            return std::make_shared<Process>(pid, pid, Architecture::x86_64, eid, std::move(readEnd));
        });
    });
    actualStateRequire(process && process->_resourceLifetimePipe && !process->_resources,
        "actual state Process preparation failed or collided");
    auto gateCount = [&]() {
        unsigned long long count = 0;
        Thread::kernelSync([&]() {
            count = dearust_actual_state_gate_count(kernelProcess()->_dtapeTask);
        });
        return count;
    };
    const auto initialGateCount = gateCount();
    struct Fence {
        PreparedResourceCleanupJob job;
        std::atomic<bool> done{false};
    } fence;
    fence.job.context = &fence;
    fence.job.run = [](void* raw) noexcept { static_cast<Fence*>(raw)->done = true; };
    auto drain = [&]() {
        fence.done = false;
        Thread::enqueueResourceCleanup(fence.job);
        actualStateWait([&]() { return fence.done.load(); });
    };
    const auto attempts = dearust_actual_state_lane_attempts();
    dearust_actual_state_set_lane_denial(true);
    bool caught = false;
    Thread::kernelSync([&]() {
        actualStateRequestControlFailure = true;
        try { (void)process->resourceState(); }
        catch (const std::bad_alloc&) { caught = true; }
        actualStateRequire(!actualStateRequestControlFailure && !actualStateFailNextAllocation,
            "actual state constructor/control-block injection did not execute");
    });
    drain();
    actualStateRequire(caught && actualStateControlFailures == 1 && actualStateDisposals == 1 &&
        !process->_resources, "actual control-block failure leaked or published ResourceState");
    actualStateRequire(gateCount() == initialGateCount,
        "actual control-block failure leaked real kernel-task semaphore ownership");
    Thread::kernelSync([&]() {
        state = process->resourceState();
        clockid_t clock;
        struct timespec now{};
        actualStateRequire(clock_getcpuclockid(pid, &clock) == 0 && clock_gettime(clock, &now) == 0,
            "actual state CPU clock unavailable");
        const uint64_t deadline = static_cast<uint64_t>(now.tv_sec) * 1000000000ULL + now.tv_nsec + 60000000000ULL;
        actualStateRequire(state->cpu->set_deadline(deadline, 2) == 0, "actual state future timer arm failed");
    });
    actualStateRequire(gateCount() == initialGateCount + 1,
        "successful ResourceState did not acquire exactly one real kernel-task semaphore");
    actualStateNativeDenial = true;
    process->resourceDying();
    actualStateRequire(state->dead && state->cpu->set_deadline(0, 3) == -ESRCH,
        "actual state death was deferred or allowed CPU revival");
    actualStateRequire(state->cpu->disable() == 0 && state->cpu->set_deadline(0, 4) == -ESRCH,
        "actual state disable revived dead CPU owner");
    actualStateNativeDenial = false;
    Server::sharedInstance().removeMonitor(state->lifetimeMonitor);
    {
        std::unique_lock lock(process->_rwlock);
        process->_resources.reset();
    }
    actualStateNativeDenial = true;
    state.reset();
    actualStateNativeDenial = false;
    drain();
    actualStateRequire(actualStateDisposals == 2 && actualStateNativeAttempts == 0 &&
        dearust_actual_state_lane_attempts() == attempts, "actual state cleanup allocated or failed to retire");
    actualStateRequire(gateCount() == initialGateCount,
        "actual final ResourceState release leaked real kernel-task semaphore ownership");
    dearust_actual_state_set_lane_denial(false);
    Server::sharedInstance().discardActualStateProcessMonitor(process->_pidfd->fd());
    std::weak_ptr<Process> lifetime = process;
    process->notifyDead();
    process.reset();
    actualStateWait([&]() { return lifetime.expired(); });
    printf("DEARUST_ACTUAL_STATE_RESULT {\"control_block_failures_after_real_gate\":1,"
           "\"real_state_disposals_on_cleanup_lane\":2,\"native_death_and_final_release_allocations\":0,"
           "\"cleanup_allocation_attempts\":0,\"terminal_cpu_shutdown_checked\":true,"
           "\"real_gate_count_acquired_and_restored\":true}\n");
    fflush(stdout);
}
