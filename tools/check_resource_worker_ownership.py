#!/usr/bin/env python3
"""Exact WorkQueue template startup/timeout ownership; no Server/RPC claims."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess

from patch_resource_death_work import patch_workers
from patch_resource_worker_ownership import patch_worker_ownership

COMMAND_RECORD = None


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, records, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60,
        env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1'}, **kwargs)
    records.append(dict(command=command, exit_code=result.returncode,
                        stdout=result.stdout, stderr=result.stderr))
    if COMMAND_RECORD:
        COMMAND_RECORD.write_text(json.dumps(records, indent=2) + '\n')
    return result


def no_core(): resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def instrumentation(text):
    prefix = '''#include <thread>
#include <system_error>
#include <cerrno>
extern bool workerTestLaunchFailure() noexcept;
extern void workerTestEnter() noexcept;
extern void workerTestExit() noexcept;
extern void workerTestWaiting() noexcept;
extern void workerTestWaitDone() noexcept;
extern void workerTestShutdown() noexcept;
extern void workerTestTimeout() noexcept;
struct WorkerTestActivity {
    WorkerTestActivity() noexcept { workerTestEnter(); }
    ~WorkerTestActivity() { workerTestExit(); }
};
template<class Function, class... Args>
std::thread workerTestLaunch(Function function, Args... args) {
    if (workerTestLaunchFailure()) throw std::system_error(EAGAIN, std::generic_category());
    return std::thread(function, args...);
}
'''
    changes = [
        ('void worker(bool permanent) {', 'void worker(bool permanent) {\n            WorkerTestActivity testActivity;'),
        ('else ready = _cv.wait_for(lock, temporaryWorkerWaitPeriod(), predicate);',
         'else { workerTestWaiting(); ready = _cv.wait_for(lock, temporaryWorkerWaitPeriod(), predicate); workerTestWaitDone(); }'),
        ('if (!ready) {', 'if (!ready) {\n                    workerTestTimeout();'),
        ('decltype(_workerThreads) joining;', 'decltype(_workerThreads) joining;\n            workerTestShutdown();'),
        ('std::thread(&WorkQueue::worker, this, permanent)', 'workerTestLaunch(&WorkQueue::worker, this, permanent)'),
        ('std::chrono::seconds temporaryWorkerWaitPeriod()', 'std::chrono::milliseconds temporaryWorkerWaitPeriod()'),
        ('return std::chrono::seconds(15);', 'return std::chrono::milliseconds(2);'),
        ('std::thread::hardware_concurrency()', '4u'),
    ]
    for old, new in changes:
        if text.count(old) != 1: raise ValueError('native worker instrumentation anchor: ' + old)
        text = text.replace(old, new, 1)
    return prefix + text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    global COMMAND_RECORD
    COMMAND_RECORD = output / 'commands.json'
    tools = Path(__file__).resolve().parent
    source = args.source.resolve() / 'internal-include/darlingserver/workers.hpp'
    fixture = tools / 'fixtures/resource-worker-ownership-native.cpp'
    reserved_fixture = tools / 'fixtures/resource-death-work-native.cpp'
    inputs = [source, fixture, reserved_fixture, Path(__file__).resolve(),
              tools / 'patch_resource_death_work.py', tools / 'patch_resource_worker_ownership.py']
    hashes = {str(path): digest(path) for path in inputs}
    pin = subprocess.check_output(['git', '-C', str(args.baseline.resolve()), 'rev-parse', 'HEAD'], text=True).strip()
    committed = subprocess.check_output(['git', '-C', str(args.baseline.resolve()), 'show',
        pin + ':internal-include/darlingserver/workers.hpp'], text=True)
    (output / 'original-workers.hpp').write_text(committed)
    fixed = patch_worker_ownership(patch_workers(source.read_text()))
    records = []
    results = {}
    for variant, flags in [('native', ['-O2']), ('sanitized', ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all'])]:
        include = output / variant / 'include/darlingserver'
        include.mkdir(parents=True)
        (include / 'config.hpp').write_text('#pragma once\nnamespace DarlingServer { struct Config { static constexpr bool singleThreadedWorkQueue = false; }; }\n')
        (include / 'workers.hpp').write_text(instrumentation(fixed))
        binary = output / variant / 'worker-ownership'
        command = ['clang++', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror', *flags,
            '-I' + str(include.parent), str(fixture), '-o', str(binary)]
        run(command, records).check_returncode()
        execution = run([str(binary)], records)
        execution.check_returncode()
        results[variant] = json.loads(execution.stdout)
        # Existing exact reserved-dispatch regression, with its single permanent
        # worker and shutdown barrier. All queue logic uses the new helper.
        (include / 'config.hpp').write_text('#pragma once\nnamespace DarlingServer { struct Config { static constexpr bool singleThreadedWorkQueue = true; }; }\n')
        reserved = 'extern void reservedTestDying() noexcept;\n' + fixed.replace(
            'joining.swap(_workerThreads);', 'joining.swap(_workerThreads); reservedTestDying();')
        (include / 'workers.hpp').write_text(reserved)
        reserved_binary = output / variant / 'reserved-dispatch'
        run(['clang++', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror', *flags,
            '-I' + str(include.parent), str(reserved_fixture), '-o', str(reserved_binary)], records).check_returncode()
        run([str(reserved_binary)], records).check_returncode()
    original_include = output / 'original/include/darlingserver'
    original_include.mkdir(parents=True)
    (original_include / 'workers.hpp').write_text(committed)
    (original_include / 'config.hpp').write_text('#pragma once\nnamespace DarlingServer { struct Config { static constexpr bool singleThreadedWorkQueue = false; }; }\n')
    original_binary = output / 'original/worker-ownership'
    run(['clang++', '-std=c++17', '-pthread', '-O2', '-DWORKER_TEST_ORIGINAL', '-I' + str(original_include.parent),
        str(fixture), '-o', str(original_binary)], records).check_returncode()
    aborts = []
    for nth in range(12):
        execution = run([str(original_binary), '--constructor-nth', str(nth)], records, preexec_fn=no_core)
        if execution.returncode == -signal.SIGABRT: aborts.append(nth)
        elif execution.returncode not in (0, 2): raise ValueError('unexpected original constructor result')
    if not aborts: raise ValueError('committed constructor termination not reproduced')
    fixed_corresponding = {}
    for nth in aborts:
        execution = run([str(output / 'native/worker-ownership'), '--constructor-nth', str(nth)], records)
        if execution.returncode not in (0, 2): raise ValueError('fixed corresponding constructor failure')
        fixed_corresponding[nth] = 'caught-and-joined' if execution.returncode == 0 else 'no-allocation-at-this-index'
    unchanged = all(digest(path) == expected for path, expected in hashes.items())
    if not unchanged: raise ValueError('worker source or test input mutated')
    report = dict(passed=True, scope=__doc__, inputs_unchanged=unchanged, input_sha256=hashes,
        original=dict(repository=str(args.baseline.resolve()), commit=pin,
            workers_sha256=hashlib.sha256(committed.encode()).hexdigest(), constructor_abort_indices=aborts,
            fixed_corresponding_results=fixed_corresponding),
        fixed_header_sha256=hashlib.sha256(fixed.encode()).hexdigest(), results=results,
        test_adjustments=['temporary timeout 2ms, maximum four workers in timeout fixture only',
            'worker activity/timeout/shutdown observation barriers',
            'EAGAIN injected at std::thread construction boundary; actual successful workers are native std::threads',
            'ordinary C++ operator-new injection is caller-thread scoped'], commands=records)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, original_constructor_aborts=aborts, results=results)))


if __name__ == '__main__': main()
