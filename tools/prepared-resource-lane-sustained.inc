    // A real kernel microthread owns the C mutex. The dedicated lane must
    // suspend through the dtape hook, preserve its stack/current owner, then
    // resume after the ordinary owner unlocks. Native fallback locking would
    // fail this observed-suspension requirement.
    struct LaneMutexBarrier {
        pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
        pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
        std::atomic<bool> held{false}, holderDone{false}, waiterDone{false};
        bool release = false;
    } barrier;
    dearust_lane_mutex_initialize();
    Thread::kernelAsync([&]() {
        actualCleanupRequire(Thread::currentThread() != nullptr,
                             "mutex holder lacks real kernel microthread");
        dearust_lane_mutex_lock();
        actualCleanupRequire(pthread_mutex_lock(&barrier.lock) == 0, "holder barrier lock failed");
        barrier.held = true;
        while (!barrier.release)
            actualCleanupRequire(pthread_cond_wait(&barrier.changed, &barrier.lock) == 0,
                                 "holder barrier wait failed");
        actualCleanupRequire(pthread_mutex_unlock(&barrier.lock) == 0, "holder barrier unlock failed");
        dearust_lane_mutex_unlock();
        barrier.holderDone = true;
    });
    actualCleanupWait([&]() { return barrier.held.load(); });
    PreparedResourceCleanupJob mutexJob;
    mutexJob.context = &barrier;
    mutexJob.run = [](void* raw) noexcept {
        auto* barrier = static_cast<LaneMutexBarrier*>(raw);
        auto before = Thread::currentThread();
        dearust_lane_mutex_lock();
        actualCleanupRequire(before && Thread::currentThread() == before &&
            Thread::resourceCleanupLane(before.get()), "resumed mutex waiter lost microthread owner");
        dearust_lane_mutex_unlock();
        barrier->waiterDone = true;
    };
    const auto suspensions = laneForcedSuspensions.load();
    actualCleanupCheckAllocations = true;
    Thread::enqueueResourceCleanup(mutexJob);
    actualCleanupWait([&]() { return laneForcedSuspensions > suspensions; });
    actualCleanupRequire(pthread_mutex_lock(&barrier.lock) == 0, "release barrier lock failed");
    barrier.release = true;
    actualCleanupRequire(pthread_mutex_unlock(&barrier.lock) == 0, "release barrier unlock failed");
    actualCleanupRequire(pthread_cond_signal(&barrier.changed) == 0, "release barrier signal failed");
    actualCleanupWait([&]() { return barrier.holderDone && barrier.waiterDone; });
    actualCleanupRequire(actualCleanupAllocationAttempts == 0,
        "real dtape mutex suspension attempted an interposed allocation");
    actualCleanupCheckAllocations = false;
    actualCleanupRequire(pthread_mutex_destroy(&barrier.lock) == 0, "barrier mutex destruction failed");
    actualCleanupRequire(pthread_cond_destroy(&barrier.changed) == 0, "barrier condition destruction failed");
    printf("DEARUST_LANE_MUTEX_RESULT %u\n", laneForcedSuspensions.load() - suspensions);
    fflush(stdout);

    // Test-only concurrent producers stress the same intrusive queue used by
    // real cleanup. Storage is owned before submission; every callback verifies
    // the live kernel-microthread context and increments a nonallocating count.
    struct LaneContention {
        std::atomic<unsigned> completed{0};
    } contention;
    std::vector<PreparedResourceCleanupJob> jobs(4096);
    for (auto& job: jobs) {
        job.context = &contention;
        job.run = [](void* context) noexcept {
            auto current = Thread::currentThread();
            actualCleanupRequire(current && Thread::resourceCleanupLane(current.get()) &&
                Thread::resourceCleanupLoggingSuppressed(), "contended job lost cleanup context");
            static_cast<LaneContention*>(context)->completed.fetch_add(1);
        };
    }
    std::vector<std::thread> producers;
    actualCleanupCheckAllocations = true;
    for (unsigned producer = 0; producer < 4; ++producer) {
        producers.emplace_back([&, producer]() {
            for (unsigned index = producer; index < jobs.size(); index += 4)
                Thread::enqueueResourceCleanup(jobs[index]);
        });
    }
    for (auto& producer: producers) producer.join();
    actualCleanupWait([&]() { return contention.completed == jobs.size(); });
    actualCleanupRequire(actualCleanupAllocationAttempts == 0,
        "contended cleanup queue attempted an interposed allocation");
    actualCleanupCheckAllocations = false;
