#!/usr/bin/env python3
"""Prepare kernel-async queue acceptance without publishing a failed submission.

This changes synchronous submission only. The runner's front/copy/pop/execution
body is unchanged; executing callback ownership is a separate inactive scope.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('kernel async submission anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


QUEUE = '''// A staged list node makes callback acceptance a nonthrowing splice. Keep
// the existing runner queue API and its executing callback ownership unchanged.
struct KernelAsyncSubmissionQueue:
    std::queue<std::function<void()>, std::list<std::function<void()>>> {
    void accept(std::list<std::function<void()>>& pending) noexcept {
        this->c.splice(this->c.end(), pending);
    }
};
static KernelAsyncSubmissionQueue kernelAsyncRunnerQueue;'''
GUARD = '''
// This C interlock has no native-thread ownership. Every fallible synchronous
// preparation uses lexical release, and foreign acquisition happens outside it.
struct KernelAsyncQueueGuard {
    KernelAsyncQueueGuard() noexcept { libsimple_lock_lock(&kernelAsyncRunnerQueueLock); }
    ~KernelAsyncQueueGuard() { libsimple_lock_unlock(&kernelAsyncRunnerQueueLock); }
    KernelAsyncQueueGuard(const KernelAsyncQueueGuard&) = delete;
    KernelAsyncQueueGuard& operator=(const KernelAsyncQueueGuard&) = delete;
};
static size_t kernelAsyncPermanentRunnersStarting = 0;
'''
SUBMIT = '''void DarlingServer::Thread::kernelAsync(std::function<void()> fn) {
    static const bool inited = []() {
        auto* semaphore = dtape_semaphore_create(Process::kernelProcess()->_dtapeTask, 0);
        if (!semaphore) throw std::bad_alloc();
        kernelAsyncRunnerQueueSempahore = semaphore;
        return true;
    }();
    (void)inited;

    std::list<std::function<void()>> pending;
    pending.emplace_back(std::move(fn));
    bool createRunner = false;
    bool permanent = false;
    {
        KernelAsyncQueueGuard guard;
        // Reserve every permanent owner slot before any foreign Thread exists.
        permanentKernelAsyncRunners.reserve(MAX_PERMANENT_KERNEL_RUNNERS);
        createRunner = kernelAsyncRunnersAvailable == 0;
        if (createRunner) {
            permanent = permanentKernelAsyncRunners.size() +
                kernelAsyncPermanentRunnersStarting < MAX_PERMANENT_KERNEL_RUNNERS;
            if (permanent) ++kernelAsyncPermanentRunnersStarting;
        }
    }

    std::shared_ptr<Thread> runner;
    if (createRunner) {
        try {
            runner = threadRegistry().registerPrepared([permanent](EternalID eid) {
                auto thread = std::make_shared<Thread>(KernelThreadConstructorTag(), eid);
                // Startup callback allocation precedes Registry's C acquisition.
                // A failed factory must not leave a strong self-capture cycle.
                std::weak_ptr<Thread> weak = thread;
                thread->setupKernelThread([weak, permanent]() {
                    kernelAsyncRunnerThreadWorker(permanent, weak.lock());
                });
                return thread;
            }, true);
        } catch (...) {
            KernelAsyncQueueGuard guard;
            if (permanent) --kernelAsyncPermanentRunnersStarting;
            // Existing permanent runners eventually drain accepted work. An
            // optional growth failure cannot become a failed accepted dispatch.
            // Bootstrap has no such guarantee and must reject before acceptance.
            if (permanentKernelAsyncRunners.empty()) throw;
            kernelAsyncRunnerQueue.accept(pending);
        }
    }
    if (!pending.empty()) {
        KernelAsyncQueueGuard guard;
        if (runner && permanent) {
            --kernelAsyncPermanentRunnersStarting;
            permanentKernelAsyncRunners.push_back(runner); // capacity reserved
        }
        kernelAsyncRunnerQueue.accept(pending);
    }
    // The callback has been accepted and every remaining step is nonallocating.
    // setupKernelThread completed inside the factory; resume uses its reserved
    // dispatch node. A worker may start before this semaphore increment safely.
    if (runner) runner->resume();
    dtape_semaphore_up(kernelAsyncRunnerQueueSempahore);
};
'''


def patch_kernel_async_submission(files):
    files = dict(files)
    source = files['thread.cpp']
    source = '#include <list>\n' + source
    source = once(source,
        'static std::queue<std::function<void()>> kernelAsyncRunnerQueue;', QUEUE)
    source = once(source, '#define MAX_PERMANENT_KERNEL_RUNNERS 10',
        '#define MAX_PERMANENT_KERNEL_RUNNERS 10\n' + GUARD)
    start = source.index('void DarlingServer::Thread::kernelAsync(std::function<void()> fn) {')
    end = source.index('void DarlingServer::Thread::kernelSync(', start)
    files['thread.cpp'] = source[:start] + SUBMIT + '\n' + source[end:]
    return files
