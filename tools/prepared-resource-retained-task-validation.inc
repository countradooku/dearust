// Actual retained-C-reference test. Included inside the Server native validation
// after every child Thread has retired and before retiring its Process.
{
    auto* retainedTask = process->_dtapeTask.load();
    const auto retainedEID = process->eternalID();
    const auto disposalsBefore = actualCleanupTaskDisposals.load();
    dtape_task_retain(retainedTask);
    auto monitor = _monitors.back();
    removeMonitor(monitor); monitor.reset();
    actualCleanupRequire(processRegistry().unregisterEntry(process), "cannot retire retained-task Process");
    std::weak_ptr<Process> retainedOwner = process;
    process->abortKernelHandles();
    std::atomic<bool> barrierDone{false};
    PreparedResourceCleanupJob barrier;
    barrier.context = &barrierDone;
    barrier.run = [](void* raw) noexcept { static_cast<std::atomic<bool>*>(raw)->store(true); };
    Thread::enqueueResourceCleanup(barrier);
    actualCleanupWait([&]() { return barrierDone.load(); });
    actualCleanupRequire(process->_dtapeTask.load() == retainedTask &&
        dearust_prepared_task_saved_eid(retainedTask) == retainedEID &&
        actualCleanupTaskDisposals.load() == disposalsBefore,
        "initial C owner release invalidated a retained task or disposed its context early");
    process.reset();
    actualCleanupRequire(!retainedOwner.expired(), "retained C task did not pin its C++ context owner");
    struct RetainedRelease {
        dtape_task_t* task;
        std::weak_ptr<Process>* owner;
        std::atomic<bool> done{false};
    } release{retainedTask, &retainedOwner};
    PreparedResourceCleanupJob finalRelease;
    finalRelease.context = &release;
    finalRelease.run = [](void* raw) noexcept {
        auto* state = static_cast<RetainedRelease*>(raw);
        auto owner = state->owner->lock();
        actualCleanupRequire(owner != nullptr, "retained task lost owner before final release");
        dtape_task_release(state->task);
        actualCleanupRequire(owner->_dtapeTask.load() == nullptr,
            "final C disposer did not clear its exact borrowed task field");
        owner.reset();
        state->done.store(true);
    };
    Thread::enqueueResourceCleanup(finalRelease);
    actualCleanupWait([&]() { return release.done.load() && retainedOwner.expired(); });
    actualCleanupRequire(actualCleanupTaskDisposals.load() == disposalsBefore + 1,
        "retained task did not dispose exactly once on its final C release");
    _monitorsWaitingToDie.clear();
}
