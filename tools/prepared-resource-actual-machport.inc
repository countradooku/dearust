// Native validation runs after real dtape and cleanup-lane startup, before
// accepting calls. Callback delivery is posted under the actual XNU mqueue lock.
extern "C" uint32_t dearust_actual_mach_allocate_port();
extern "C" uint32_t dearust_actual_mach_allocate_portset();
extern "C" int dearust_actual_mach_join_portset(uint32_t, uint32_t);
extern "C" int dearust_actual_mach_send(uint32_t);
extern "C" int dearust_actual_mach_destroy_port(uint32_t);
extern "C" unsigned dearust_actual_mach_task_references();
extern "C" int dearust_actual_mach_invalid_attach();
extern "C" void dearust_actual_mach_post(void*);
namespace {
std::atomic<void*> actualMachTarget{nullptr};
std::atomic<void*> actualMachOwner{nullptr};
std::atomic<void*> actualMachWaiterWaiting{nullptr};
std::atomic<bool> actualMachSnapshotPause{false}, actualMachSnapshotEntered{false}, actualMachSnapshotRelease{false};
std::atomic<bool> actualMachPromotionPause{false}, actualMachPromotionEntered{false}, actualMachPromotionRelease{false};
std::atomic<bool> actualMachDetachEntered{false};
std::atomic<unsigned> actualMachDisposals{0}, actualMachAllocationAttempts{0};
std::atomic<bool> actualMachDenyCleanupAllocations{false};
std::atomic<bool> actualMachDenyLiveCallback{false};
thread_local bool actualMachDenyThisThread = false;
void actualMachRequire(bool condition, const char* text) {
    if (!condition) { write(STDERR_FILENO, text, strlen(text)); write(STDERR_FILENO, "\n", 1); _Exit(2); }
}
template<class Predicate> void actualMachWait(Predicate predicate, const char* timeout = "actual Mach-port lifecycle wait timed out") {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (!predicate()) {
        actualMachRequire(std::chrono::steady_clock::now() < deadline, timeout);
        std::this_thread::yield();
    }
}
}

bool dearust_actual_cleanup_allocation_denied() noexcept {
    if (!actualMachDenyThisThread && !(actualMachDenyCleanupAllocations &&
            DarlingServer::Thread::resourceCleanupLoggingSuppressed())) return false;
    ++actualMachAllocationAttempts;
    return true;
}

extern "C" void dearust_actual_mach_snapshot(void* handle) {
    if (handle != actualMachTarget.load() || !actualMachSnapshotPause) return;
    actualMachSnapshotEntered = true;
    actualMachWait([] { return actualMachSnapshotRelease.load(); });
}
extern "C" void dearust_actual_mach_waiter_waiting(void* handle) { actualMachWaiterWaiting = handle; }
extern "C" void dearust_actual_mach_promoted(void* owner) {
    if (actualMachDenyLiveCallback) actualMachDenyThisThread = true;
    if (owner != actualMachOwner.load() || !actualMachPromotionPause) return;
    actualMachPromotionEntered = true;
    actualMachWait([] { return actualMachPromotionRelease.load(); });
}
extern "C" void dearust_actual_mach_notified(void*) { actualMachDenyThisThread = false; }
extern "C" void dearust_actual_mach_detach_begin(void* handle) {
    if (handle == actualMachTarget.load()) actualMachDetachEntered = true;
}
extern "C" void dearust_actual_mach_disposed(void* handle) {
    auto current = DarlingServer::Thread::currentThread();
    actualMachRequire(current && DarlingServer::Thread::resourceCleanupLane(current.get()),
        "actual Mach-port disposal ran outside cleanup kernel lane");
    if (handle == actualMachTarget.load()) ++actualMachDisposals;
}

void DarlingServer::Server::_runActualMachportValidation() {
    uint32_t port = 0;
    unsigned referencesBefore = 0;
    bool invalidBalanced = false;
    Thread::kernelSync([&] {
        port = dearust_actual_mach_allocate_port();
        referencesBefore = dearust_actual_mach_task_references();
        invalidBalanced = dearust_actual_mach_invalid_attach();
    });
    actualMachRequire(port && invalidBalanced, "actual invalid Mach-port attach leaked task ownership");
    const auto baselineMonitors = _monitors.size();
    for (int stage = 0; stage < 2; ++stage) {
        auto channel = std::make_shared<Kqchan::MachPort>(Process::kernelProcess(), port, 0, 0, 0);
        int peer = -1;
        Thread::kernelSync([&] { peer = channel->setup(); });
        actualMachRequire(peer >= 0 && channel->_dtapeKqchan, "actual Mach-port setup failed");
        void* handle = channel->_dtapeKqchan;
        actualMachTarget = handle;
        actualMachOwner = channel.get();
        actualMachDetachEntered = false;
        actualMachSnapshotEntered = false; actualMachSnapshotRelease = false;
        actualMachPromotionEntered = false; actualMachPromotionRelease = false;
        actualMachSnapshotPause = stage == 0; actualMachPromotionPause = stage == 1;
        actualMachDenyLiveCallback = stage == 1;
        const unsigned disposed = actualMachDisposals.load();
        std::atomic<bool> postDone{false};
        Thread::kernelAsync([handle, &postDone] { dearust_actual_mach_post(handle); postDone = true; });
        if (stage == 0) actualMachWait([] { return actualMachSnapshotEntered.load(); });
        else actualMachWait([] { return actualMachPromotionEntered.load(); });
        std::weak_ptr<Kqchan::MachPort> weak = channel;
        actualMachDenyCleanupAllocations = true;
        actualMachDenyThisThread = true;
        channel.reset();
        actualMachDenyThisThread = false;
        if (stage == 0) {
            actualMachWait([] { return actualMachDetachEntered.load(); });
            actualMachRequire(weak.expired() && actualMachDisposals == disposed,
                "actual detach freed snapshotted weak context before mqueue callback drained");
            actualMachSnapshotRelease = true;
        } else {
            actualMachRequire(!weak.expired() && !actualMachDetachEntered,
                "actual promoted callback did not retain C++ Mach-port owner");
            actualMachPromotionRelease = true;
        }
        actualMachWait([&] { return postDone.load() && weak.expired() && actualMachDisposals == disposed + 1; });
        actualMachDenyCleanupAllocations = false;
        actualMachSnapshotPause = false; actualMachPromotionPause = false;
        actualMachDenyLiveCallback = false;
        if (stage == 1) {
            dserver_kqchan_call_notification_t notification = {};
            actualMachRequire(recv(peer, &notification, sizeof(notification), MSG_DONTWAIT) == sizeof(notification) &&
                notification.header.number == dserver_kqchan_msgnum_notification,
                "actual live callback did not send its reserved readiness notification");
        }
        close(peer);
        // This validator has not called epoll_wait. No returned monitor pointer
        // needs to remain pinned after the corresponding callback has finished.
        _monitorsWaitingToDie.clear();
        actualMachRequire(_monitors.size() == baselineMonitors, "actual Mach-port monitor survived retirement");
    }
    unsigned referencesAfter = 0;
    bool destroyed = false;
    uint32_t portset = 0;
    Thread::kernelSync([&] { portset = dearust_actual_mach_allocate_portset(); });
    actualMachRequire(portset != 0, "actual Mach port-set allocation failed");
    bool joined = false;
    Thread::kernelSync([&] { joined = dearust_actual_mach_join_portset(port, portset) == 0; });
    actualMachRequire(joined, "actual Mach port-set member insertion failed");
    auto setChannel = std::make_shared<Kqchan::MachPort>(Process::kernelProcess(), portset, 0, 0, 0);
    int setPeer = -1;
    Thread::kernelSync([&] { setPeer = setChannel->setup(); });
    actualMachRequire(setPeer >= 0 && setChannel->_dtapeKqchan, "actual Mach port-set waiter setup failed");
    actualMachTarget = setChannel->_dtapeKqchan;
    actualMachOwner = setChannel.get();
    actualMachDetachEntered = false;
    actualMachSnapshotEntered = false; actualMachSnapshotRelease = false;
    actualMachSnapshotPause = true;
    actualMachWait([&] { return actualMachWaiterWaiting.load() == actualMachTarget.load(); }, "port-set waiter never registered wait");
    bool sent = false;
    Thread::kernelSync([&] { sent = dearust_actual_mach_send(port) == 0; });
    actualMachRequire(sent, "actual ordinary Mach message send failed");
    actualMachWait([] { return actualMachSnapshotEntered.load(); }, "port-set waiter callback never entered");
    const unsigned setDisposed = actualMachDisposals.load();
    actualMachDenyCleanupAllocations = true;
    actualMachDenyThisThread = true;
    setChannel.reset();
    actualMachDenyThisThread = false;
    actualMachWait([] { return actualMachDetachEntered.load(); }, "port-set detach never entered");
    actualMachRequire(actualMachDisposals == setDisposed, "actual port-set waiter callback did not block detach completion");
    actualMachSnapshotRelease = true;
    actualMachWait([&] { return actualMachDisposals == setDisposed + 1; }, "port-set detach failed to drain active waiter callback");
    actualMachDenyCleanupAllocations = false;
    actualMachSnapshotPause = false;
    close(setPeer);
    _monitorsWaitingToDie.clear();
    actualMachRequire(_monitors.size() == baselineMonitors, "actual Mach port-set monitor survived retirement");
    Thread::kernelSync([&] {
        destroyed = dearust_actual_mach_destroy_port(port) == 0 && dearust_actual_mach_destroy_port(portset) == 0;
    });
    actualMachWait([&] {
        Thread::kernelSync([&] { referencesAfter = dearust_actual_mach_task_references(); });
        return referencesAfter == referencesBefore;
    }, "actual Mach-port task references did not return after waiter/message retirement");
    actualMachRequire(destroyed && referencesBefore == referencesAfter,
        "actual Mach-port retirement changed task reference baseline");
    actualMachRequire(actualMachAllocationAttempts == 0, "actual Mach-port cleanup attempted allocation");
    printf("DEARUST_ACTUAL_MACHPORT_RESULT {\"actual_port\":true,\"actual_portset_waiter_detach\":true,\"actual_portset_callback_drain\":true,\"live_callback_notification_allocation_denied\":true,\"invalid_attach_balanced\":true,\"callback_race_cases\":3,\"task_references\":%u,\"cleanup_allocation_attempts\":%u}\n",
        referencesAfter, actualMachAllocationAttempts.load());
    fflush(stdout);
    _Exit(0);
}
