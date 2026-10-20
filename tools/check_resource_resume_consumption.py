#!/usr/bin/env python3
"""Exact wake/admission/consumption gates with real reserved WorkQueue retries.

Foreign waits and saved ucontext bodies are modeled. No RPC, guest, ownership,
closure lifetime, fault injection, or comprehensive scheduler safety claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_resource_resume_consumption import patch_resume_consumption


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def method(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]
def once(source, old, new):
    if source.count(old) != 1: raise ValueError('proof anchor: ' + old[:100])
    return source.replace(old, new, 1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    tools = Path(__file__).resolve().parent
    paths = {'thread.cpp': args.source.resolve()/'src/thread.cpp',
        'thread.hpp': args.source.resolve()/'internal-include/darlingserver/thread.hpp',
        'workers.hpp': args.source.resolve()/'internal-include/darlingserver/workers.hpp'}
    fixture = tools/'fixtures/resource-resume-consumption-native.cpp.in'
    patch = tools/'patch_resource_resume_consumption.py'
    hashes = {str(path): digest(path) for path in [*paths.values(), fixture, patch, Path(__file__).resolve()]}
    before = {key: path.read_text() for key, path in paths.items()}
    after = patch_resume_consumption(before)
    records = []; results = {}
    def run(command):
        r = subprocess.run(command, capture_output=True, text=True, timeout=30,
            env={**os.environ, 'ASAN_OPTIONS':'detect_leaks=1'})
        records.append(dict(command=command, exit_code=r.returncode, stdout=r.stdout, stderr=r.stderr))
        (out/'commands.json').write_text(json.dumps(records, indent=2)+'\n')
        r.check_returncode(); return r
    for variant, flags in [('native',['-O2']), ('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        inc = out/variant/'include/darlingserver'; inc.mkdir(parents=True)
        (inc/'config.hpp').write_text('#pragma once\nnamespace DarlingServer{struct Config{static constexpr bool singleThreadedWorkQueue=true;};}\n')
        (inc/'workers.hpp').write_text(once(before['workers.hpp'], '        void pushReserved(',
            '''        bool testReservedIdle(ReservedWorkNode<WorkItem>& node) { std::unique_lock lock(_queueMutex); return !node.queued && !node.running; }
        void pushReserved('''))
        for label, files in [('before',before), ('after',after)]:
            source = files['thread.cpp']
            work = method(source, 'void DarlingServer::Thread::doWork() {', 'void DarlingServer::Thread::suspend(')
            entry = work[work.index('    if (_impersonationActive)'):work.index('\t_running = true;')]
            # Both real selection branches consume the same wake permission.
            consume = '\t\t\t_suspended = false;'
            if label == 'after': consume = '\t\t\t_resumePending = false;\n'+consume
            if work.count(consume) != 2: raise ValueError('selection fragment count')
            suspend = '\t_suspended = true;'
            if label == 'after': suspend = '\t_resumePending = false;\n'+suspend
            actual_suspend = method(source, 'void DarlingServer::Thread::suspend(', 'void DarlingServer::Thread::resume(')
            actual_setup = method(source, 'void DarlingServer::Thread::setupKernelThread(', 'void DarlingServer::Thread::startKernelThread(')
            if actual_suspend.count(suspend) != 1 or actual_setup.count(suspend) != 1:
                raise ValueError('suspension/reset publication fragments changed')
            text = fixture.read_text()
            slots = {'ENTRY':entry, 'CONSUME':consume, 'SUSPEND':suspend,
                'RESUME':method(source,'void DarlingServer::Thread::resume(', 'void DarlingServer::Thread::terminate('),
                'HANDOFF':method(source,'void DarlingServer::Thread::_handoffPendingWorkLocked() noexcept {', '\n}')+'\n}'}
            for key, value in slots.items(): text = text.replace('@'+key+'@', value)
            src = out/variant/(label+'.cpp'); src.write_text(text); binary=out/variant/label
            command = ['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror', *flags]
            if label=='before': command += ['-DBEFORE_RESUME_CONSUMPTION']
            run(command+['-I'+str(inc.parent),str(src),'-o',str(binary)])
            results[variant+'-'+label] = json.loads(run([str(binary)]).stdout)
    if any(digest(Path(path)) != value for path,value in hashes.items()): raise ValueError('input mutated')
    (out/'generated-thread.cpp').write_text(after['thread.cpp'])
    (out/'generated-thread.hpp').write_text(after['thread.hpp'])
    report = dict(passed=True, scope=__doc__, input_sha256=hashes, inputs_unchanged=True,
        results=results, model_boundary=[
            'Exact selected resume method, doWork entry guards, both selection reset fragments, suspension publication, and handoff method.',
            'Actual selected WorkQueue reserved-node queued/running/again transitions on a native worker.',
            'Foreign C waits and ucontext execution modeled by counters and controlled second suspension.',
            'Dedicated native cleanup lane remains unchanged and excluded from reserved-queue admission gating.',
            'No assertion about delayed foreign wake sources: this consumes queued wake permission and rejects scheduling retries for unwoken waits.'], commands=records)
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=True, results=results)))
if __name__ == '__main__': main()
