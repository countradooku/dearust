// Native semantic isolation of three real C task/thread generations belonging to
// the same logical C++ owners. Wrapper semaphores are retired before replacement;
// this exercises opaque context ownership rather than the separate exec protocol.
{
    auto generationProcess = processRegistry().registerPreparedIfAbsent(childPID, [&](EternalID eid) {
        return std::make_shared<Process>(childPID, childPID, Process::Architecture::x86_64, eid);
    });
    auto generationThread = threadRegistry().registerPreparedIfAbsent(childPID, [&](EternalID eid) {
        return std::make_shared<Thread>(generationProcess, childPID, eid);
    });
    const auto taskDisposalsBefore = actualCleanupTaskDisposals.load();
    const auto threadDisposalsBefore = actualCleanupThreadDisposals.load();
    struct GenerationRelease {
        dtape_task_t* task = nullptr;
        dtape_thread_t* thread = nullptr;
        dtape_semaphore_t* semaphores[5] = {};
        bool dying = false;
        std::atomic<bool> done{false};
    } release;
    auto releaseOnLane = [&]() {
        release.done.store(false);
        PreparedResourceCleanupJob job;
        job.context = &release;
        job.run = [](void* raw) noexcept {
            auto* state = static_cast<GenerationRelease*>(raw);
            for (auto& semaphore: state->semaphores) {
                if (semaphore) dtape_semaphore_destroy(semaphore);
                semaphore = nullptr;
            }
            if (state->thread) {
                if (state->dying) dtape_thread_dying(state->thread);
                dtape_thread_release(state->thread);
            }
            if (state->task) {
                if (state->dying) dtape_task_dying(state->task);
                dtape_task_release(state->task);
            }
            state->done.store(true);
        };
        Thread::enqueueResourceCleanup(job);
        actualCleanupWait([&]() { return release.done.load(); });
        release.task = nullptr; release.thread = nullptr;
    };
    {
        std::unique_lock processLock(generationProcess->_rwlock);
        std::unique_lock threadLock(generationThread->_rwlock);
        release.semaphores[0] = generationProcess->_dtapeForkWaitSemaphore;
        generationProcess->_dtapeForkWaitSemaphore = nullptr;
        release.semaphores[1] = generationThread->_s2cPerformSempahore;
        release.semaphores[2] = generationThread->_s2cReplySempahore;
        release.semaphores[3] = generationThread->_s2cInterruptEnterSemaphore;
        release.semaphores[4] = generationThread->_s2cInterruptExitSemaphore;
        generationThread->_s2cPerformSempahore = generationThread->_s2cReplySempahore = nullptr;
        generationThread->_s2cInterruptEnterSemaphore = generationThread->_s2cInterruptExitSemaphore = nullptr;
    }
    releaseOnLane();
    dtape_task_t* tasks[3] = {generationProcess->_dtapeTask.load()};
    dtape_thread_t* threads[3] = {generationThread->_dtapeThread.load()};
    for (unsigned generation = 1; generation != 3; ++generation) {
        int taskStatus = -1, threadStatus = -1;
        tasks[generation] = generationProcess->createOwnedTask(tasks[generation - 1], Process::Architecture::x86_64, &taskStatus);
        actualCleanupRequire(tasks[generation] && taskStatus == 0, "cannot acquire real replacement task generation");
        threads[generation] = generationThread->createOwnedThread(tasks[generation], &threadStatus);
        actualCleanupRequire(threads[generation] && threadStatus == 0, "cannot acquire real replacement thread generation");
        dtape_task_retain(tasks[generation - 1]);
        dtape_thread_retain(threads[generation - 1]);
        generationProcess->_dtapeTask.store(tasks[generation]);
        generationThread->_dtapeThread.store(threads[generation]);
        release.task = tasks[generation - 1]; release.thread = threads[generation - 1]; release.dying = true;
        releaseOnLane();
        actualCleanupRequire(actualCleanupTaskDisposals.load() == taskDisposalsBefore &&
            actualCleanupThreadDisposals.load() == threadDisposalsBefore &&
            generationProcess->_dtapeTask.load() == tasks[generation] &&
            generationThread->_dtapeThread.load() == threads[generation],
            "retiring retained generation disposed early or altered replacement handles");
    }
    std::weak_ptr<Process> processWeak = generationProcess;
    std::weak_ptr<Thread> threadWeak = generationThread;
    auto monitor = _monitors.back(); removeMonitor(monitor); monitor.reset();
    actualCleanupRequire(threadRegistry().unregisterEntry(generationThread) &&
        processRegistry().unregisterEntry(generationProcess), "cannot unregister generation owners");
    generationThread->abortKernelHandles(); generationProcess->abortKernelHandles();
    releaseOnLane(); // FIFO barrier after both newest generation cleanup jobs.
    actualCleanupRequire(generationProcess->_dtapeTask.load() == nullptr &&
        generationThread->_dtapeThread.load() == nullptr &&
        actualCleanupTaskDisposals.load() == taskDisposalsBefore + 1 &&
        actualCleanupThreadDisposals.load() == threadDisposalsBefore + 1,
        "newest generation did not dispose its exact owners");
    generationThread.reset(); generationProcess.reset();
    actualCleanupRequire(!processWeak.expired() && !threadWeak.expired(),
        "retired generation contexts failed to retain owners after newest disposal");
    for (unsigned generation = 2; generation != 0; --generation) {
        release.task = tasks[generation - 1]; release.thread = threads[generation - 1]; release.dying = false;
        releaseOnLane();
        actualCleanupRequire(actualCleanupTaskDisposals.load() == taskDisposalsBefore + 4 - generation &&
            actualCleanupThreadDisposals.load() == threadDisposalsBefore + 4 - generation,
            "retired generation did not dispose exactly once in newest-first order");
        if (generation != 1) {
            auto processOwner = processWeak.lock(); auto threadOwner = threadWeak.lock();
            actualCleanupRequire(processOwner && threadOwner &&
                processOwner->_dtapeTask.load() == nullptr && threadOwner->_dtapeThread.load() == nullptr,
                "old context disposal altered current empty handles or lost an older owner");
        }
    }
    actualCleanupWait([&]() { return processWeak.expired() && threadWeak.expired(); });
    _monitorsWaitingToDie.clear();
}
