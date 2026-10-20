#!/usr/bin/env python3
"""Owned ResourceState final release and irreversible CPU death shutdown.

Apply after existing prepared transforms. No file IO: keys are
resource-server-state.inc, resource-cpu-timer.inc and process.hpp.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('resource state death anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_resource_state_death(files):
    files = dict(files)
    timer = files['resource-cpu-timer.inc']
    timer = once(timer, '    std::shared_ptr<Arm> current_;',
                 '    std::shared_ptr<Arm> current_;\n    bool stopped_ = false;')
    timer = once(timer, '    ~CpuTimer() { disable(); }', '    ~CpuTimer() { shutdown(); }')
    timer = once(timer, '    int disable() noexcept {', '''    // Process death is terminal. Serialize with any in-flight arm preparation,
    // then retire its result before returning; disable() never clears stopped_.
    void shutdown() noexcept {
        std::lock_guard<std::mutex> guard(lock_);
        stopped_ = true;
        retire(current_);
        current_.reset();
        diagnostics_->error.store(0, std::memory_order_release);
    }
    int disable() noexcept {''')
    timer = once(timer, '''        std::lock_guard<std::mutex> guard(lock_);
        if (clock_error_) return -clock_error_;''', '''        std::lock_guard<std::mutex> guard(lock_);
        if (stopped_) return -ESRCH;
        if (clock_error_) return -clock_error_;''')
    files['resource-cpu-timer.inc'] = timer
    state = files['resource-server-state.inc']
    state = once(state, '#include "resource-cpu-timer.inc"',
                 '#include "resource-cpu-timer.inc"\n#include "prepared-resource-cleanup.hpp"')
    state = once(state, 'struct ResourceState {', '''struct ResourceState {
    // The object itself owns all final-release queue storage before acquiring
    // any foreign handle. A shared_ptr control-block allocation failure invokes
    // this same deleter, and the raw object stays alive until its job executes.
    PreparedResourceCleanupJob cleanupJob;
    static void destroyOnKernelLane(void* raw) noexcept {
        delete static_cast<ResourceState*>(raw);
    }''')
    state = once(state, '        limits.fill(resourceInfinity);', '''        Thread::requireResourceCleanupReady();
        cleanupJob.run = destroyOnKernelLane;
        cleanupJob.context = this;
        limits.fill(resourceInfinity);''')
    state = once(state, '''    ~ResourceState() {
        // The shared_ptr custom deleter schedules this destructor in a kernel
        // microthread, even if the last reservation was removed by an epoll callback.
        if (gate) dtape_semaphore_destroy(gate);
    }''', '''    ~ResourceState() noexcept {
        // Only the prepared lane deletes fully constructed states, including
        // when shared_ptr could not allocate its control block.
        cpu->shutdown();
        if (gate) dtape_semaphore_destroy(gate);
    }''')
    state = once(state, '        [](ResourceState* state) { Thread::kernelAsync([state]() { delete state; }); });',
                 '''        [](ResourceState* state) noexcept {
            Thread::enqueueResourceCleanup(state->cleanupJob);
        });''')
    state = once(state, '''void Process::resourceDying() {
    std::shared_ptr<ResourceState> state;
    { std::shared_lock lock(_rwlock); state = _resources; }
    if (!state) return;
    Thread::kernelAsync([state]() {
        // Do not wait for a prepared fork gate: parent death plus inherited pipe
        // EOF own reservation cleanup, and pidfd prevents late signal reuse.
        state->dead = true;
        state->cpu->disable();
    });
}''', '''void Process::resourceDying() noexcept {
    std::shared_ptr<ResourceState> state;
    { std::shared_lock lock(_rwlock); state = _resources; }
    if (!state) return;
    // Host timer APIs need no dtape context or resource gate. Terminal timer
    // shutdown serializes against callers that passed ResourceGuard before death.
    state->dead.store(true, std::memory_order_release);
    state->cpu->shutdown();
}''')
    files['resource-server-state.inc'] = state
    files['process.hpp'] = once(files['process.hpp'], '    void resourceDying();',
                                '    void resourceDying() noexcept;')
    return files
