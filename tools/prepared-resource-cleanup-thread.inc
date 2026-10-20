// The native lane is deliberately dedicated: blocking its idle condition
// variable does not block the ordinary worker pool. Its kernel microthread has
// an established stack before readiness is published. Actual dtape lock waits
// may suspend it; their resume hooks signal this lane without WorkQueue growth.
namespace {
struct PreparedResourceCleanupState {
    DarlingServer::PreparedResourceCleanupQueue jobs;
    pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
    pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
    bool ready = false;
    bool resume = false;
    std::shared_ptr<DarlingServer::Thread> thread;
    std::thread* native = nullptr;
};
static std::atomic<PreparedResourceCleanupState*> preparedResourceCleanupState{nullptr};
static thread_local bool preparedResourceCleanupSuppressLog = false;

static void preparedResourceCleanupCheck(int error) noexcept {
    if (error) std::abort();
}

static void preparedResourceCleanupWake() noexcept {
    auto* state = preparedResourceCleanupState.load(std::memory_order_acquire);
    if (!state) std::abort();
    preparedResourceCleanupCheck(pthread_mutex_lock(&state->lock));
    state->resume = true;
    preparedResourceCleanupCheck(pthread_mutex_unlock(&state->lock));
    preparedResourceCleanupCheck(pthread_cond_signal(&state->changed));
}

static void preparedResourceCleanupSubmit(DarlingServer::PreparedResourceCleanupJob& job) noexcept {
    auto* state = preparedResourceCleanupState.load(std::memory_order_acquire);
    if (!state) std::abort();
    state->jobs.push(job);
}
}

bool DarlingServer::Thread::resourceCleanupLoggingSuppressed() noexcept {
    return preparedResourceCleanupSuppressLog;
}

bool DarlingServer::Thread::resourceCleanupLane(const Thread* thread) noexcept {
    auto* state = preparedResourceCleanupState.load(std::memory_order_acquire);
    return state && state->thread.get() == thread;
}

void DarlingServer::Thread::requireResourceCleanupReady() {
    auto* state = preparedResourceCleanupState.load(std::memory_order_acquire);
    if (!state) throw std::logic_error("resource cleanup lane is not initialized");
    preparedResourceCleanupCheck(pthread_mutex_lock(&state->lock));
    const bool ready = state->ready;
    preparedResourceCleanupCheck(pthread_mutex_unlock(&state->lock));
    if (!ready) throw std::logic_error("resource cleanup lane is not ready");
}

void DarlingServer::Thread::enqueueResourceCleanup(PreparedResourceCleanupJob& job) noexcept {
    preparedResourceCleanupSubmit(job);
}

void DarlingServer::Thread::initializeResourceCleanup() {
    // Startup-only initialization, before accepting calls. All allocations and
    // native-thread/stack creation happen before ready. Startup failure remains
    // fatal; it is not represented as successful guest registration rollback.
    static const bool initialized = []() {
        auto* state = new PreparedResourceCleanupState;
        state->thread = threadRegistry().registerPrepared([&](EternalID eid) {
            return std::make_shared<Thread>(KernelThreadConstructorTag{}, eid);
        }, true);
        state->thread->setupKernelThread([state]() {
            preparedResourceCleanupCheck(pthread_mutex_lock(&state->lock));
            state->ready = true;
            preparedResourceCleanupCheck(pthread_mutex_unlock(&state->lock));
            preparedResourceCleanupCheck(pthread_cond_broadcast(&state->changed));
            for (;;) {
                auto* job = state->jobs.take();
                job->run(job->context);
            }
        });
        preparedResourceCleanupState.store(state, std::memory_order_release);
        state->native = new std::thread([state]() {
            preparedResourceCleanupSuppressLog = true;
            for (;;) {
                state->thread->doWork();
                preparedResourceCleanupCheck(pthread_mutex_lock(&state->lock));
                while (!state->resume)
                    preparedResourceCleanupCheck(pthread_cond_wait(&state->changed, &state->lock));
                state->resume = false;
                preparedResourceCleanupCheck(pthread_mutex_unlock(&state->lock));
            }
        });
        preparedResourceCleanupCheck(pthread_mutex_lock(&state->lock));
        while (!state->ready)
            preparedResourceCleanupCheck(pthread_cond_wait(&state->changed, &state->lock));
        preparedResourceCleanupCheck(pthread_mutex_unlock(&state->lock));
        return true;
    }();
    (void)initialized;
}

void DarlingServer::Thread::prepareKernelHandles() {
    if (_dtapeThread) {
        // Bootstrap constructors acquire handles before the cleanup lane exists.
        _selfReference = shared_from_this();
        return;
    }
    requireResourceCleanupReady();
    _selfReference = shared_from_this();
    try {
        std::shared_lock processLock(_process->_rwlock);
        if (_process->_dead || !_process->_dtapeTask)
            throw std::system_error(ESRCH, std::generic_category());
        _dtapeThread = dtape_thread_create(_process->_dtapeTask, _nstid, this);
        if (!_dtapeThread) throw std::bad_alloc();
        _s2cPerformSempahore = dtape_semaphore_create(_process->_dtapeTask, 1);
        if (!_s2cPerformSempahore) throw std::bad_alloc();
        _s2cReplySempahore = dtape_semaphore_create(_process->_dtapeTask, 0);
        if (!_s2cReplySempahore) throw std::bad_alloc();
        _s2cInterruptEnterSemaphore = dtape_semaphore_create(_process->_dtapeTask, 0);
        if (!_s2cInterruptEnterSemaphore) throw std::bad_alloc();
        _s2cInterruptExitSemaphore = dtape_semaphore_create(_process->_dtapeTask, 0);
        if (!_s2cInterruptExitSemaphore) throw std::bad_alloc();
    } catch (...) {
        abortKernelHandles();
        throw;
    }
}

void DarlingServer::Thread::abortKernelHandles() noexcept {
    // Registry owns the object while preparing/aborting. Published death sets
    // _selfReference before scheduling. Never acquire shared ownership here:
    // exhaustion rollback must use only ownership established before acquisition.
    std::unique_lock lock(_rwlock);
    if (_resourceCleanupQueued) return;
    if (!_dtapeThread && !_selfReference) return;
    if (!_selfReference) std::abort();
    _resourceCleanupQueued = true;
    _resourceCleanupJob.run = _resourceCleanupRun;
    _resourceCleanupJob.context = this;
    lock.unlock();
    enqueueResourceCleanup(_resourceCleanupJob);
}

void DarlingServer::Thread::_resourceCleanupRun(void* context) noexcept {
    auto* raw = static_cast<Thread*>(context);
    // The final release may invoke _dispose synchronously. Keep the object/job
    // alive until this callback finishes even if that clears its self-reference.
    auto self = raw->_selfReference;
    dtape_semaphore_t* semaphores[4];
    dtape_thread_t* thread;
    {
        std::unique_lock lock(self->_rwlock);
        semaphores[0] = self->_s2cPerformSempahore;
        semaphores[1] = self->_s2cReplySempahore;
        semaphores[2] = self->_s2cInterruptEnterSemaphore;
        semaphores[3] = self->_s2cInterruptExitSemaphore;
        self->_s2cPerformSempahore = nullptr;
        self->_s2cReplySempahore = nullptr;
        self->_s2cInterruptEnterSemaphore = nullptr;
        self->_s2cInterruptExitSemaphore = nullptr;
        thread = self->_dtapeThread;
        self->_dtapeThread = nullptr;
    }
    for (auto* semaphore: semaphores)
        if (semaphore) dtape_semaphore_destroy(semaphore);
    if (thread) {
        // Also establish terminal C state for unpublished rollback. The dying
        // operation is idempotent when published notifyDead already called it.
        dtape_thread_dying(thread);
        dtape_thread_release(thread);
    }
    else self->_selfReference = nullptr;
    // A C handle with outstanding references retains _selfReference until its
    // eventual context_dispose callback, even after this job has returned.
}
