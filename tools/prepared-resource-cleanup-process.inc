void DarlingServer::Process::prepareKernelHandles() {
    if (_dtapeTask) {
        _selfReference = shared_from_this();
        return;
    }
    Thread::requireResourceCleanupReady();
    _selfReference = shared_from_this();
    try {
        auto parent = _parentProcess.lock();
        std::shared_lock<std::shared_mutex> parentLock;
        if (parent) parentLock = std::shared_lock(parent->_rwlock);
        if (parent && (parent->_dead || !parent->_dtapeTask))
            throw std::system_error(ESRCH, std::generic_category());
        // Cleanup removes the parent's task under this same lock. Creation
        // either completes using that live handle or rejects retired ownership.
        _dtapeTask = dtape_task_create(parent ? parent->_dtapeTask : nullptr,
            _nspid, this, static_cast<dserver_rpc_architecture_t>(_architecture));
        if (!_dtapeTask) throw std::bad_alloc();
        _dtapeForkWaitSemaphore = dtape_semaphore_create(_dtapeTask, 0);
        if (!_dtapeForkWaitSemaphore) throw std::bad_alloc();
    } catch (...) {
        abortKernelHandles();
        throw;
    }
}

void DarlingServer::Process::abortKernelHandles() noexcept {
    std::unique_lock lock(_rwlock);
    if (_resourceCleanupQueued) return;
    if (!_dtapeTask && !_selfReference) return;
    if (!_selfReference) std::abort();
    _resourceCleanupQueued = true;
    _resourceCleanupJob.run = _resourceCleanupRun;
    _resourceCleanupJob.context = this;
    lock.unlock();
    Thread::enqueueResourceCleanup(_resourceCleanupJob);
}

void DarlingServer::Process::_resourceCleanupRun(void* context) noexcept {
    auto* raw = static_cast<Process*>(context);
    auto self = raw->_selfReference;
    dtape_semaphore_t* semaphore;
    dtape_task_t* task;
    {
        std::unique_lock lock(self->_rwlock);
        semaphore = self->_dtapeForkWaitSemaphore;
        self->_dtapeForkWaitSemaphore = nullptr;
        task = self->_dtapeTask;
        self->_dtapeTask = nullptr;
    }
    if (semaphore) dtape_semaphore_destroy(semaphore);
    if (task) {
        dtape_task_dying(task);
        dtape_task_release(task);
    }
    else self->_selfReference = nullptr;
}
