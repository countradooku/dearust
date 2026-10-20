"""Reserved work dispatch for microthread resumption; transform owned sources only.

The ordinary dynamic work queue retains its existing worker growth policy.
Resumes use an embedded node and existing permanent workers; initial RPC dispatch
retains the ordinary dynamic queue. This also covers live peers woken by death.
Queued requests coalesce; requests during execution guarantee another pass.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('reserved death work anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


NODE = '''
    template<class WorkItem>
    struct ReservedWorkNode {
        WorkItem value{};
        ReservedWorkNode* next = nullptr;
        void* owner = nullptr;
        bool queued = false;
        bool running = false;
        bool again = false;
    };
'''

WORKER = '''
        void worker(bool permanent) {
            std::optional<WorkItem> workItem;
            ReservedWorkNode<WorkItem>* reserved = nullptr;
            std::unique_lock lock(_queueMutex, std::defer_lock);
            for (;;) {
                lock.lock();
                ++_threadsAvailable;
                const auto predicate = [&]() {
                    return _dying || _reservedHead || !_workItems.empty();
                };
                bool ready;
                if (permanent) { _cv.wait(lock, predicate); ready = true; }
                else ready = _cv.wait_for(lock, temporaryWorkerWaitPeriod(), predicate);
                --_threadsAvailable;
                if (_dying) return;
                if (!ready) {
                    auto thisThread = std::move(_workerThreads[std::this_thread::get_id()]);
                    _workerThreads.erase(thisThread.get_id());
                    thisThread.detach();
                    return;
                }
                if (_reservedHead) {
                    reserved = _reservedHead;
                    _reservedHead = reserved->next;
                    if (!_reservedHead) _reservedTail = nullptr;
                    reserved->next = nullptr;
                    reserved->queued = false;
                    reserved->running = true;
                    workItem = reserved->value;
                } else {
                    reserved = nullptr;
                    workItem = std::move(_workItems.front());
                    _workItems.pop();
                }
                lock.unlock();
                _workerFunction(std::move(workItem.value()));
                // The embedded value keeps the node alive until this bookkeeping
                // finishes. Keep a separate owner while clearing that value, so
                // a final destructor never runs under the queue lock.
                if (reserved) {
                    workItem = reserved->value;
                    lock.lock();
                    reserved->running = false;
                    if (reserved->again && !_dying) {
                        reserved->again = false;
                        reserved->queued = true;
                        if (_reservedTail) _reservedTail->next = reserved;
                        else _reservedHead = reserved;
                        _reservedTail = reserved;
                    } else {
                        reserved->again = false;
                        reserved->owner = nullptr;
                        reserved->value = WorkItem{};
                    }
                    lock.unlock();
                    _cv.notify_one();
                }
                workItem.reset();
            }
        };
'''

PUSH_RESERVED = '''
        void pushReserved(ReservedWorkNode<WorkItem>& node, WorkItem value) noexcept {
            std::unique_lock lock(_queueMutex);
            if (_dying || (node.owner && node.owner != this)) std::terminate();
            if (node.running) {
                node.again = true;
            } else if (!node.queued) {
                node.owner = this;
                node.value = std::move(value);
                node.next = nullptr;
                node.queued = true;
                if (_reservedTail) _reservedTail->next = &node;
                else _reservedHead = &node;
                _reservedTail = &node;
            }
            lock.unlock();
            _cv.notify_one();
        }
'''


def patch_workers(workers):
    workers = once(workers, '\ttemplate<class WorkItem>\n\tclass WorkQueue {',
                   NODE + '\ttemplate<class WorkItem>\n\tclass WorkQueue {')
    workers = once(workers, '\t\tbool _dying = false;', '''\t\tbool _dying = false;
        ReservedWorkNode<WorkItem>* _reservedHead = nullptr;
        ReservedWorkNode<WorkItem>* _reservedTail = nullptr;''')
    begin = workers.index('\t\tvoid worker(bool permanent) {')
    end = workers.index('\n\tpublic:', begin)
    workers = workers[:begin] + WORKER + workers[end:]
    workers = once(workers, '\t\tvoid push(WorkItem workItem) {',
                   PUSH_RESERVED + '\t\tvoid push(WorkItem workItem) {')
    workers = once(workers, '''\t\t\tfor (auto& [id, thread]: _workerThreads) {
\t\t\t\tthread.join();
\t\t\t}''', '''\t\t\tfor (auto& [id, thread]: _workerThreads) {
\t\t\t\tthread.join();
\t\t\t}
            // All workers have stopped. Break queued intrusive ownership cycles
            // without allocating, and never destroy an item under the mutex.
            while (_reservedHead) {
                auto* node = _reservedHead;
                _reservedHead = node->next;
                node->next = nullptr;
                node->queued = false;
                node->owner = nullptr;
                WorkItem value = std::move(node->value);
            }
            _reservedTail = nullptr;''')
    return workers


def patch_death_work(files):
    files = dict(files)
    files['workers.hpp'] = patch_workers(files['workers.hpp'])
    header = files['thread.hpp']
    header = '#include <darlingserver/workers.hpp>\n' + header
    header = once(header, '\t\tfriend class Process;',
                   '\t\tfriend class Process;\n        friend class Server;')
    header = once(header, '\t\tbool _dead = false;',
                   '\t\tbool _dead = false;\n        ReservedWorkNode<std::shared_ptr<Thread>> _deathWork;')
    files['thread.hpp'] = header
    files['server.hpp'] = once(files['server.hpp'],
        '\t\tvoid scheduleThread(std::shared_ptr<Thread> thread);',
        '\t\tvoid scheduleThread(std::shared_ptr<Thread> thread);\n'
        '        void scheduleResumedThread(std::shared_ptr<Thread> thread) noexcept;')
    files['server.cpp'] += '''
void DarlingServer::Server::scheduleResumedThread(std::shared_ptr<Thread> thread) noexcept {
    _workQueue.pushReserved(thread->_deathWork, thread);
}
'''
    text = files['thread.cpp']
    begin = text.index('void DarlingServer::Thread::resume() {')
    end = text.index('void DarlingServer::Thread::terminate()', begin)
    text = text[:begin] + '''void DarlingServer::Thread::resume() {
    if (resourceCleanupLane(this)) {
        preparedResourceCleanupWake();
        return;
    }
    std::shared_ptr<Thread> owner;
    {
        std::shared_lock lock(_rwlock);
        if (!_suspended) return;
        owner = _dead ? _selfReference : shared_from_this();
        if (!owner) std::terminate();
    }
    Server::sharedInstance().scheduleResumedThread(std::move(owner));
};

''' + text[end:]
    # Do not allocate logging state while holding the manually managed raw lock.
    text = once(text, '\t\tmicrothreadLog.debug() << _tid << "(" << _nstid << "): execution was deferred" << microthreadLog.endLog;\n', '')
    text = once(text, '\t\tmicrothreadLog.warning() << _tid << "(" << _nstid << "): attempt to re-run already running microthread on another thread" << microthreadLog.endLog;\n', '')
    files['thread.cpp'] = text
    stack = files['stack-pool.cpp']
    stack = once(stack, '\tfor (size_t i = 0; i < _idleStackCount; ++i) {',
        '\t_stacks.reserve(_idleStackCount);\n\tfor (size_t i = 0; i < _idleStackCount; ++i) {')
    stack = once(stack, 'if (_stacks.size() > _idleStackCount)',
                   'if (_stacks.size() >= _idleStackCount)')
    files['stack-pool.cpp'] = stack
    return files
