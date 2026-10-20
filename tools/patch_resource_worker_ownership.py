#!/usr/bin/env python3
"""Own all worker threads across fallible startup and concurrent shutdown.

Apply only after patch_resource_death_work.patch_workers. Reserved dispatch and
worker execution retain that transform; this changes thread-map ownership only.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('worker ownership anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


START = '''
        // _queueMutex excludes worker entry/timeout and other launchers. Reserve
        // every map allocation before a joinable std::thread can exist locally.
        void startWorkerLocked(bool permanent) {
            static_assert(noexcept(std::hash<std::thread::id>{}(std::thread::id{})),
                "worker node insertion requires a nonthrowing thread-id hash");
            _workerThreads.reserve(_workerThreads.size() + 1);
            auto position = _workerThreads.try_emplace(std::thread::id{}).first;
            auto node = _workerThreads.extract(position);
            // If thread creation throws, node owns only a default, nonjoinable
            // thread. A launched worker cannot pass _queueMutex until inserted.
            node.mapped() = std::thread(&WorkQueue::worker, this, permanent);
            node.key() = node.mapped().get_id();
            auto inserted = _workerThreads.insert(std::move(node));
            if (!inserted.inserted) std::terminate(); // live IDs cannot collide
        }
'''


CONSTRUCTOR = '''        WorkQueue(std::function<void(WorkItem)> workerFunction):
            _workerFunction(std::move(workerFunction))
        {
            decltype(_workerThreads) started;
            std::unique_lock lock(_queueMutex);
            try {
                for (size_t i = 0; i < minimumThreads(); ++i)
                    startWorkerLocked(true);
            } catch (...) {
                _dying = true;
                started.swap(_workerThreads);
                lock.unlock();
                _cv.notify_all();
                for (auto& item: started) item.second.join();
                throw;
            }
        };
'''


DESTRUCTOR = '''        ~WorkQueue() {
            decltype(_workerThreads) joining;
            {
                std::unique_lock lock(_queueMutex);
                _dying = true;
                // Timeout retirement tests _dying under this same mutex. Once
                // swapped, no worker can erase from the map we iterate below.
                joining.swap(_workerThreads);
            }
            _cv.notify_all();
            for (auto& item: joining) item.second.join();
            // All workers stopped; release intrusive owners outside the mutex.
            while (_reservedHead) {
                auto* node = _reservedHead;
                _reservedHead = node->next;
                node->next = nullptr;
                node->queued = false;
                node->owner = nullptr;
                WorkItem value = std::move(node->value);
                (void)value;
            }
            _reservedTail = nullptr;
        };
'''


PUSH = '''        void push(WorkItem workItem) {
            std::unique_lock lock(_queueMutex);
            if (_dying) throw std::system_error(ECANCELED, std::generic_category());
            _workItems.push(std::move(workItem));
            if (_threadsAvailable == 0 && _workerThreads.size() < maximumThreads()) {
                // Acceptance has already succeeded. Growth is optional because
                // permanent workers remain available to drain the queued work.
                // A growth failure cannot be reported as a failed dispatch.
                try { startWorkerLocked(false); }
                catch (...) {}
            }
            lock.unlock();
            _cv.notify_one();
        };
'''


def patch_worker_ownership(workers):
    workers = '#include <system_error>\n#include <cerrno>\n#include <utility>\n' + workers
    workers = once(workers, '\n        void worker(bool permanent) {', START + '\n        void worker(bool permanent) {')
    workers = once(workers, '''                    auto thisThread = std::move(_workerThreads[std::this_thread::get_id()]);
                    _workerThreads.erase(thisThread.get_id());
                    thisThread.detach();''', '''                    auto found = _workerThreads.find(std::this_thread::get_id());
                    if (found == _workerThreads.end()) std::terminate();
                    auto retired = _workerThreads.extract(found);
                    retired.mapped().detach();''')
    begin = workers.index('\t\tWorkQueue(std::function<void(WorkItem)> workerFunction):')
    end = workers.index('\t\tWorkQueue(const WorkQueue&) = delete;', begin)
    workers = workers[:begin] + CONSTRUCTOR + DESTRUCTOR + '\n' + workers[end:]
    begin = workers.index('\t\tvoid push(WorkItem workItem) {')
    end = workers.index('\n\t};\n};', begin)
    return workers[:begin] + PUSH + workers[end:]


def patch_worker_ownership_files(files):
    files = dict(files)
    files['workers.hpp'] = patch_worker_ownership(files['workers.hpp'])
    return files
