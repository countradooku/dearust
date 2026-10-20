// Test additions compiled with the actual transformed CPU engine and original
// real-process timer fixture. These hooks only coordinate ordinary host calls.
static thread_local long cppFailAfter = -1;
void* operator new(std::size_t size) {
    if (cppFailAfter == 0) throw std::bad_alloc();
    if (cppFailAfter > 0) --cppFailAfter;
    if (void* raw = std::malloc(size ? size : 1)) return raw;
    throw std::bad_alloc();
}
void operator delete(void* raw) noexcept { std::free(raw); }
void operator delete(void* raw, std::size_t) noexcept { std::free(raw); }
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete[](void* raw) noexcept { ::operator delete(raw); }
void operator delete[](void* raw, std::size_t) noexcept { ::operator delete(raw); }
static std::atomic<bool> pauseSettime{false}, settimeEntered{false}, releaseSettime{false};
static void shutdownPauseSettime() noexcept {
    if (pauseSettime.exchange(false)) {
        settimeEntered = true;
        while (!releaseSettime.load()) std::this_thread::yield();
    }
}
// SHUTDOWN TESTS
static void runShutdownRegression(Child& child) {
    static_assert(noexcept(std::declval<DearustResources::CpuTimer&>().shutdown()));
    {
        DearustResources::CpuTimer timer(child.pid, child.descriptor->fd, child.descriptor, 800);
        const auto deadline = cpu(child.pid) + 60000000000ULL;
        check(timer.set_deadline(deadline, 1) == 0, "initial future CPU arm");
        check(timer.disable() == 0 && timer.set_deadline(deadline, 2) == 0,
              "ordinary disable remains reversible");
        pauseSettime = true; settimeEntered = false; releaseSettime = false;
        std::atomic<int> result{1};
        std::atomic<bool> shutdownEntered{false}, shutdownReturned{false};
        std::thread setter([&]() { result = timer.set_deadline(deadline, 3); });
        child.await([&]() { return settimeEntered.load(); });
        std::thread death([&]() {
            shutdownEntered = true;
            cppFailAfter = 0;
            timer.shutdown();
            shutdownReturned = true;
        });
        child.await([&]() { return shutdownEntered.load(); });
        check(!shutdownReturned, "shutdown cannot bypass in-flight preparation lock");
        releaseSettime = true;
        setter.join(); death.join();
        check(result == 0 && shutdownReturned, "in-flight preparation drains before shutdown returns");
        cppFailAfter = 0;
        check(timer.set_deadline(0, 4) == -ESRCH && timer.set_deadline(deadline, 5) == -ESRCH,
              "terminal shutdown rejects immediate and future rearm without allocation");
        check(timer.disable() == 0 && timer.set_deadline(deadline, 6) == -ESRCH,
              "ordinary disable cannot revive shutdown timer");
        timer.shutdown();
        cppFailAfter = -1;
    }
    for (unsigned round = 0; round < 16; ++round) {
        DearustResources::CpuTimer timer(child.pid, child.descriptor->fd, child.descriptor, 801 + round);
        const auto deadline = cpu(child.pid) + 60000000000ULL;
        std::atomic<bool> begin{false}, stopped{false};
        std::atomic<unsigned> armed{0};
        std::vector<std::thread> setters;
        for (unsigned worker = 0; worker < 4; ++worker) setters.emplace_back([&, worker]() {
            while (!begin) std::this_thread::yield();
            for (unsigned index = 0; index < 128; ++index) {
                const auto result = timer.set_deadline(deadline, 1 + worker * 128 + index);
                check(result == 0 || result == -ESRCH, "concurrent arm returned unexpected error");
                if (result == 0) ++armed;
            }
            while (!stopped) std::this_thread::yield();
            cppFailAfter = 0;
            for (unsigned index = 0; index < 128; ++index)
                check(timer.set_deadline(deadline, index) == -ESRCH, "post-death concurrent rearm succeeded");
        });
        begin = true;
        child.await([&]() { return armed > 0; });
        cppFailAfter = 0;
        timer.shutdown();
        cppFailAfter = -1;
        stopped = true;
        for (auto& setter: setters) setter.join();
        check(timer.set_deadline(0, 999) == -ESRCH, "stopped timer lost terminal state");
    }
    std::cout << "terminal timer shutdown: deterministic in-flight arm, 16 four-setter races, "
                 "8192 allocation-prohibited post-death rearm checks passed\n";
}
