#!/usr/bin/env python3
"""Exercise the exact generated resume body and its publication lock boundary."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from patch_resource_generation_resume import RESUME


SOURCE = r'''
#include <atomic>
#include <cassert>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <thread>
#include <cstdio>
struct dtape_thread_t { unsigned generation; };
static std::atomic<unsigned> wakes{0};
static void preparedResourceCleanupWake() { ++wakes; }
namespace DarlingServer {
class Thread;
class Server {
public:
    std::function<void(std::shared_ptr<Thread>)> schedule;
    static Server& sharedInstance() { static Server server; return server; }
    void scheduleResumedThread(std::shared_ptr<Thread> thread) { schedule(std::move(thread)); }
};
class Thread: public std::enable_shared_from_this<Thread> {
public:
    std::shared_mutex _rwlock;
    std::atomic<dtape_thread_t*> _dtapeThread{nullptr};
    bool _suspended = true;
    bool _dead = false;
    std::shared_ptr<Thread> _selfReference;
    bool lane = false;
    static bool resourceCleanupLane(const Thread* thread) { return thread->lane; }
    void resume(dtape_thread_t* expectedGeneration = nullptr);
};
}
@RESUME@
int main() {
    using namespace DarlingServer;
    dtape_thread_t oldHandle{0}, newHandle{1};
    auto thread = std::make_shared<Thread>();
    unsigned scheduled = 0;
    Server::sharedInstance().schedule = [&](auto) { ++scheduled; };
    thread->_dtapeThread = &newHandle;
    thread->resume(&oldHandle); assert(scheduled == 0);
    thread->resume(&newHandle); thread->resume(); assert(scheduled == 2);
    thread->_suspended = false; thread->resume(&newHandle); assert(scheduled == 2);
    thread->_suspended = true;
    thread->_dead = true; thread->_selfReference = thread;
    thread->resume(&newHandle); assert(scheduled == 3);
    thread->_dead = false; thread->_selfReference.reset();
    thread->lane = true;
    thread->resume(&oldHandle); assert(wakes == 0);
    thread->resume(&newHandle); thread->resume(); assert(wakes == 2 && scheduled == 3);
    thread->lane = false;
    for (unsigned attempt = 0; attempt != 64; ++attempt) {
        std::mutex barrierLock;
        std::condition_variable barrier;
        bool enqueueEntered = false, releaseEnqueue = false;
        std::atomic<bool> writerStarted{false}, writerFinished{false};
        thread->_dtapeThread = &oldHandle;
        Server::sharedInstance().schedule = [&](auto item) {
            assert(item->_dtapeThread.load() == &oldHandle);
            std::unique_lock lock(barrierLock);
            enqueueEntered = true; barrier.notify_all();
            barrier.wait(lock, [&] { return releaseEnqueue; });
            assert(item->_dtapeThread.load() == &oldHandle);
            ++scheduled;
        };
        std::thread callback([&] { thread->resume(&oldHandle); });
        {
            std::unique_lock lock(barrierLock);
            barrier.wait(lock, [&] { return enqueueEntered; });
        }
        // Enqueue is still executing. Publication must be excluded here.
        assert(!thread->_rwlock.try_lock());
        std::thread publisher([&] {
            writerStarted = true;
            std::unique_lock lock(thread->_rwlock);
            thread->_dtapeThread = &newHandle;
            writerFinished = true;
        });
        while (!writerStarted.load()) std::this_thread::yield();
        assert(!writerFinished.load());
        {
            std::unique_lock lock(barrierLock);
            releaseEnqueue = true; barrier.notify_all();
        }
        callback.join(); publisher.join();
        const auto beforeStale = scheduled;
        Server::sharedInstance().schedule = [&](auto) { ++scheduled; };
        thread->resume(&oldHandle); assert(scheduled == beforeStale);
        thread->resume(&newHandle); assert(scheduled == beforeStale + 1);
    }
    std::puts("{\"publication_interleavings\":64,\"stale_resume_rejected\":true,\"cleanup_wake_checked\":true}");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / 'generation-resume.cpp'
    source.write_text(SOURCE.replace('@RESUME@', RESUME))
    results = {}
    for name, flags in [('native', []), ('sanitized', ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'])]:
        binary = output / name
        command = ['/usr/bin/clang++', '-std=c++17', '-pthread', '-O1', '-g', *flags, str(source), '-o', str(binary)]
        subprocess.run(command, check=True)
        run = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
        (output / (name + '.stdout')).write_text(run.stdout)
        (output / (name + '.stderr')).write_text(run.stderr)
        results[name] = dict(command=command, result=json.loads(run.stdout))
    module = Path(__file__).with_name('patch_resource_generation_resume.py')
    report = dict(scope=__doc__, results=results,
        module_sha256=hashlib.sha256(module.read_bytes()).hexdigest())
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(results))


if __name__ == '__main__': main()
