// A separately allocated capsule owns either the new uncommitted generation or
// the old committed generation. Its allocation precedes every foreign handle.
extern "C" void dearust_dtape_thread_abort_partial(dtape_thread_t*);
extern "C" void dearust_dtape_task_abort_partial(dtape_task_t*);
struct DarlingServer::Process::PreparedExecRetirement {
    PreparedResourceCleanupJob job;
    dtape_task_t* task = nullptr;
    dtape_thread_t* thread = nullptr;
    dtape_semaphore_t* forkWait = nullptr;
    dtape_semaphore_t* perform = nullptr;
    dtape_semaphore_t* reply = nullptr;
    dtape_semaphore_t* interruptEnter = nullptr;
    dtape_semaphore_t* interruptExit = nullptr;
    bool partialTask = false;
    bool partialThread = false;
    dtape_task_t* predecessorTask = nullptr;
    dtape_thread_t* predecessorThread = nullptr;
    decltype(Process::_threads) otherThreads;

    PreparedExecRetirement() noexcept {
        job.context = this;
        job.run = [](void* raw) noexcept {
            std::unique_ptr<PreparedExecRetirement> owned(
                static_cast<PreparedExecRetirement*>(raw));
            // Other live C threads retain their original Process owner and C
            // task while finishing calls. Their ordinary death barrier arranges
            // release only after their active kernel call completes.
            for (auto& item: owned->otherThreads)
                if (auto thread = item.second.lock()) thread->notifyDead();
            owned->otherThreads.clear();
            for (auto semaphore: {owned->perform, owned->reply,
                    owned->interruptEnter, owned->interruptExit, owned->forkWait})
                if (semaphore) dtape_semaphore_destroy(semaphore);
            if (owned->thread) {
                if (owned->partialThread) dearust_dtape_thread_abort_partial(owned->thread);
                else {
                    dtape_thread_dying(owned->thread);
                    dtape_thread_release(owned->thread);
                }
            }
            if (owned->task) {
                if (owned->partialTask) dearust_dtape_task_abort_partial(owned->task);
                else {
                    dtape_task_dying(owned->task);
                    dtape_task_release(owned->task);
                }
            }
            // Pins acquired under the original owner locks protect preparation
            // even when concurrent Process death retires its owner references.
            if (owned->predecessorThread) dtape_thread_release(owned->predecessorThread);
            if (owned->predecessorTask) dtape_task_release(owned->predecessorTask);
        };
    }

    static void submit(std::unique_ptr<PreparedExecRetirement> owned) noexcept {
        auto* raw = owned.release();
        Thread::enqueueResourceCleanup(raw->job);
    }
};

void DarlingServer::Process::notifyCheckin(Architecture architecture) {
    // These owners must outlive the locks during exception unwinding: a final
    // channel or Thread destructor may unregister itself from this Process.
    std::shared_ptr<Thread> mainThread;
    std::vector<std::shared_ptr<Kqchan::Process>> listeners;
    std::vector<Kqchan::Process::PreparedExecNotification> notifications;
    decltype(_threads) replacementThreads;
    std::unique_ptr<PreparedExecRetirement> retirement;
    std::unique_lock processLock(_rwlock);
    if (_dead || _resourceCleanupQueued)
        throw std::system_error(ESRCH, std::generic_category());
    if (!_pendingReplacement) {
        if (architecture != _architecture)
            throw std::system_error(EINVAL, std::generic_category());
        processLock.unlock();
        if (auto parent = _parentProcess.lock()) {
            {
                std::unique_lock parentLock(parent->_rwlock);
                if (!parent->_dead && !parent->_resourceCleanupQueued &&
                        parent->_dtapeForkWaitSemaphore)
                    dtape_semaphore_up(parent->_dtapeForkWaitSemaphore);
            }
            parent->_notifyListeningKqchannels(NOTE_FORK, nsid());
        }
        return;
    }

    Thread::requireResourceCleanupReady();
    auto found = _threads.find(_nspid);
    mainThread = found == _threads.end() ? nullptr : found->second.lock();
    if (!mainThread) throw std::system_error(ESRCH, std::generic_category());
    std::unique_lock threadLock(mainThread->_rwlock);
    if (mainThread->_dead || mainThread->_resourceCleanupQueued)
        throw std::system_error(ESRCH, std::generic_category());

    // Reserve weak-map storage and the retirement capsule before acquiring any
    // foreign reference. C preparation can suspend or call current_task hooks,
    // so neither logical owner's lock may remain held while entering C.
    replacementThreads.emplace(mainThread->_nstid, mainThread);
    retirement = std::make_unique<PreparedExecRetirement>();
    retirement->predecessorTask = _dtapeTask.load();
    retirement->predecessorThread = mainThread->_dtapeThread.load();
    if (!retirement->predecessorTask || !retirement->predecessorThread)
        throw std::system_error(ESRCH, std::generic_category());
    dtape_task_retain(retirement->predecessorTask);
    dtape_thread_retain(retirement->predecessorThread);
    threadLock.unlock();
    processLock.unlock();
    try {
        int status = 0;
        retirement->task = createOwnedTask(retirement->predecessorTask, architecture, &status);
        retirement->partialTask = status != 0;
        if (status || !retirement->task) throw std::bad_alloc();
        retirement->thread = mainThread->createOwnedThread(retirement->task, &status);
        retirement->partialThread = status != 0;
        if (status || !retirement->thread) throw std::bad_alloc();
        retirement->forkWait = dtape_semaphore_create(retirement->task, 0);
        if (!retirement->forkWait) throw std::bad_alloc();
        retirement->perform = dtape_semaphore_create(retirement->task, 1);
        if (!retirement->perform) throw std::bad_alloc();
        retirement->reply = dtape_semaphore_create(retirement->task, 0);
        if (!retirement->reply) throw std::bad_alloc();
        retirement->interruptEnter = dtape_semaphore_create(retirement->task, 0);
        if (!retirement->interruptEnter) throw std::bad_alloc();
        retirement->interruptExit = dtape_semaphore_create(retirement->task, 0);
        if (!retirement->interruptExit) throw std::bad_alloc();
        processLock.lock();
        threadLock.lock();
        if (_dead || _resourceCleanupQueued || !_pendingReplacement ||
                mainThread->_dead || mainThread->_resourceCleanupQueued ||
                _dtapeTask.load() != retirement->predecessorTask ||
                mainThread->_dtapeThread.load() != retirement->predecessorThread)
            throw std::system_error(ESRCH, std::generic_category());
        auto currentMain = _threads.find(_nspid);
        if (currentMain == _threads.end() || currentMain->second.lock() != mainThread)
            throw std::system_error(ESRCH, std::generic_category());

        // The lock was released for C preparation. Snapshot the final complete
        // listener set now, including attachments that arrived in that interval.
        // This is the final fallible preparation, before any generation swap.
        listeners.reserve(_listeningKqchannels.size());
        notifications.reserve(_listeningKqchannels.size());
        for (auto& item: _listeningKqchannels)
            if (auto channel = item.second.lock()) listeners.push_back(std::move(channel));
        for (auto& channel: listeners)
            notifications.push_back(channel->prepareExecNotification());
    } catch (...) {
        // This is only bookkeeping preservation for terminal error delivery.
        // The client already changed its image; callers must retire it after
        // sending the reserved error reply rather than retrying this Checkin.
        if (threadLock.owns_lock()) threadLock.unlock();
        if (processLock.owns_lock()) processLock.unlock();
        PreparedExecRetirement::submit(std::move(retirement));
        throw;
    }

    // The final publication contains only pointer swaps and preallocated list
    // splices. Both Process and main Thread readers observe one generation.
    retirement->task = _dtapeTask.exchange(retirement->task);
    retirement->thread = mainThread->_dtapeThread.exchange(retirement->thread);
    if (mainThread->_running) dtape_thread_entering(mainThread->_dtapeThread);
    std::swap(retirement->forkWait, _dtapeForkWaitSemaphore);
    std::swap(retirement->perform, mainThread->_s2cPerformSempahore);
    std::swap(retirement->reply, mainThread->_s2cReplySempahore);
    std::swap(retirement->interruptEnter, mainThread->_s2cInterruptEnterSemaphore);
    std::swap(retirement->interruptExit, mainThread->_s2cInterruptExitSemaphore);
    _threads.swap(retirement->otherThreads);
    _threads.swap(replacementThreads);
    retirement->otherThreads.erase(mainThread->_nstid);
    _selfReference = shared_from_this();
    mainThread->_selfReference = mainThread;
    _preparedTaskDying = false;
    _architecture = architecture;
    _pendingReplacement = false;
    resourceExec();
    for (auto& notification: notifications) notification.commit();
    threadLock.unlock();
    processLock.unlock();
    PreparedExecRetirement::submit(std::move(retirement));
    for (auto& notification: notifications) notification.send();
};
