#!/usr/bin/env python3
"""Exact doWork scheduling gates/epilogue with modeled bodies and native workers.

No RPC, guest, Call::fromMessage, or whole-server shutdown claims. The ucontext
execution body and foreign C objects are modeled explicitly; entry, epilogue,
notifyDead, abortKernelHandles, and the worker queue use selected generated code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from patch_resource_work_handoff import patch_work_handoff

RECORD = None


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def once(text, old, new):
    if text.count(old) != 1: raise ValueError('handoff native anchor: ' + old[:100])
    return text.replace(old, new, 1)


def run(command, records):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60,
        env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1'})
    records.append(dict(command=command, exit_code=result.returncode,
                        stdout=result.stdout, stderr=result.stderr))
    RECORD.write_text(json.dumps(records, indent=2) + '\n')
    result.check_returncode()
    return result


def method(source, start, following):
    begin = source.index(start)
    return source[begin:source.index(following, begin)]


def model_work(source):
    original = method(source, 'void DarlingServer::Thread::doWork() {', 'void DarlingServer::Thread::suspend(')
    entry = original[:original.index('\t_running = true;')]
    epilogue = original[original.index('doneWorking:'):]
    return entry + '''\t_running = true;
    currentThreadVarStorage() = shared_from_this();
    _rwlock.unlock();
    modeledRun();
    _rwlock.lock();
    goto doneWorking;

''' + epilogue


def dropped_guard(source):
    work = method(source, 'void DarlingServer::Thread::doWork() {', 'void DarlingServer::Thread::suspend(')
    begin = work.index('\tif (_running) {')
    guard = work[begin:work.index('\n\t}', begin) + len('\n\t}')]
    guard = re.sub(r'^\s*(//[^\n]*|microthreadLog\.warning\(\)[^\n]*)\n', '', guard, flags=re.MULTILINE)
    return re.sub(r'\s+', '', guard)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Owned composed candidate')
    parser.add_argument('--baseline', required=True, type=Path, help='Committed original dependency repository')
    parser.add_argument('--libsimple-source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    global RECORD
    RECORD = output / 'commands.json'
    tools = Path(__file__).resolve().parent
    paths = {'thread.cpp': args.source.resolve() / 'src/thread.cpp',
             'thread.hpp': args.source.resolve() / 'internal-include/darlingserver/thread.hpp',
             'workers.hpp': args.source.resolve() / 'internal-include/darlingserver/workers.hpp'}
    fixture = tools / 'fixtures/resource-work-handoff-native.cpp.in'
    reserved_fixture = tools / 'fixtures/resource-death-work-native.cpp'
    inputs = [*paths.values(), fixture, reserved_fixture, Path(__file__).resolve(),
              tools / 'patch_resource_work_handoff.py', args.libsimple_source.resolve()]
    hashes = {str(path): digest(path) for path in inputs}
    before = {name: path.read_text() for name, path in paths.items()}
    after = patch_work_handoff(before)
    pin = subprocess.check_output(['git', '-C', str(args.baseline.resolve()), 'rev-parse', 'HEAD'], text=True).strip()
    original = subprocess.check_output(['git', '-C', str(args.baseline.resolve()), 'show', pin + ':src/thread.cpp'], text=True)
    if dropped_guard(before['thread.cpp']) != dropped_guard(original):
        raise ValueError('selected lost-request guard differs from pinned original')
    (output / 'selected-thread-before.cpp').write_text(before['thread.cpp'])
    (output / 'generated-thread-after.cpp').write_text(after['thread.cpp'])
    (output / 'generated-thread-after.hpp').write_text(after['thread.hpp'])
    records = []
    results = {}
    idle = '''        bool testReservedIdle(ReservedWorkNode<WorkItem>& node) {
            std::unique_lock lock(_queueMutex);
            return !node.queued && !node.running;
        }
'''
    for variant, flags in [('native', ['-O2']), ('sanitized', ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all'])]:
        include = output / variant / 'include/darlingserver'
        include.mkdir(parents=True)
        (include / 'config.hpp').write_text('#pragma once\nnamespace DarlingServer { struct Config { static constexpr bool singleThreadedWorkQueue = false; }; }\n')
        (include / 'workers.hpp').write_text(once(before['workers.hpp'],
            '        void pushReserved(', idle + '        void pushReserved('))
        for label, generated in [('before', before), ('after', after)]:
            source = fixture.read_text().replace('@DOWORK@', model_work(generated['thread.cpp']))
            source = source.replace('@HANDOFF@', '' if label == 'before' else
                generated['thread.cpp'][generated['thread.cpp'].index('void DarlingServer::Thread::_handoffPendingWorkLocked() noexcept {'):])
            source = source.replace('@ABORT@', method(generated['thread.cpp'],
                'void DarlingServer::Thread::abortKernelHandles() noexcept {', 'void DarlingServer::Thread::_resourceCleanupRun('))
            source = source.replace('@NOTIFY_DEAD@', method(generated['thread.cpp'],
                'void DarlingServer::Thread::notifyDead() {', 'bool DarlingServer::Thread::isDead() const'))
            path = output / variant / (label + '.cpp')
            path.write_text(source)
            binary = output / variant / label
            command = ['clang++', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror', *flags]
            if label == 'before': command.append('-DOLD_HANDOFF_BASELINE')
            command += ['-I' + str(include.parent), str(path), '-o', str(binary)]
            run(command, records)
            execution = run([str(binary)], records)
            results[variant + '-' + label] = json.loads(execution.stdout)
        # Full existing reserved-node regression on the unchanged exact selected
        # worker queue, including denial across producers and pending shutdown.
        (include / 'config.hpp').write_text('#pragma once\nnamespace DarlingServer { struct Config { static constexpr bool singleThreadedWorkQueue = true; }; }\n')
        reserved = 'extern void reservedTestDying() noexcept;\n' + once(before['workers.hpp'],
            'joining.swap(_workerThreads);', 'joining.swap(_workerThreads); reservedTestDying();')
        (include / 'workers.hpp').write_text(reserved)
        binary = output / variant / 'reserved-dispatch'
        run(['clang++', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror', *flags,
             '-I' + str(include.parent), str(reserved_fixture), '-o', str(binary)], records)
        run([str(binary)], records)
    unchanged = all(digest(path) == expected for path, expected in hashes.items())
    if not unchanged: raise ValueError('handoff proof input changed')
    report = dict(passed=True, scope=__doc__, input_sha256=hashes, inputs_unchanged=unchanged,
        original=dict(repository=str(args.baseline.resolve()), commit=pin,
            original_lost_guard_matches_selected=True), results=results,
        adjustments=['ucontext body replaced by direct modeled work/barriers only',
            'foreign C handle release and kernel unlock modeled with liveness/order assertions',
            'existing generated doWork entry/epilogue, notifyDead, abortKernelHandles and worker queue retained',
            'test-only queue idle observation under its mutex'], commands=records)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, results=results)))


if __name__ == '__main__': main()
