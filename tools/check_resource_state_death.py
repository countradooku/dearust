#!/usr/bin/env python3
"""Test exact owned state release/death and real-process CPU shutdown.

The ResourceState fixture models its dtape semaphore and kernel lane; the timer
fixture uses real owned processes, pidfds and POSIX timers. No RPC or guest runs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from patch_resource_state_death import patch_resource_state_death


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('state death fixture anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tools = Path(__file__).resolve().parent
    files = [Path(__file__).resolve(), tools / 'patch_resource_state_death.py',
             tools / 'resource-server-state.inc', tools / 'resource-cpu-timer.inc',
             tools / 'prepared-resource-cleanup.hpp', tools / 'resource-server-declarations.inc',
             tools / 'fixtures/resource-cpu-timer-native.cpp',
             tools / 'fixtures/resource-state-death-timer.inc',
             tools / 'fixtures/resource-state-death-native.cpp.in']
    hashes = {str(path): digest(path) for path in files}
    transformed = patch_resource_state_death({
        'resource-server-state.inc': (tools / 'resource-server-state.inc').read_text(),
        'resource-cpu-timer.inc': (tools / 'resource-cpu-timer.inc').read_text(),
        'process.hpp': (tools / 'resource-server-declarations.inc').read_text()})
    (output / 'resource-cpu-timer.inc').write_text(transformed['resource-cpu-timer.inc'])
    (output / 'resource-server-state.inc').write_text(transformed['resource-server-state.inc'])
    (output / 'prepared-resource-cleanup.hpp').write_text((tools / 'prepared-resource-cleanup.hpp').read_text())
    timer = (tools / 'fixtures/resource-cpu-timer-native.cpp').read_text()
    timer = once(timer, '#include "../resource-cpu-timer.inc"', '#include "resource-cpu-timer.inc"')
    helpers, tests = (tools / 'fixtures/resource-state-death-timer.inc').read_text().split('// SHUTDOWN TESTS\n')
    timer = once(timer, 'static std::atomic<int> fail_create{0}, fail_settime{0};',
                 helpers + '\nstatic std::atomic<int> fail_create{0}, fail_settime{0};')
    timer = once(timer, '    const int error = fail_settime.exchange(0);',
                 '    shutdownPauseSettime();\n    const int error = fail_settime.exchange(0);')
    timer = once(timer, 'int main(int argc,char**argv) {', tests + '\nint main(int argc,char**argv) {')
    timer = once(timer, '        DearustResources::CpuTimer invalid(child.pid,-1,{},0);',
                 '        runShutdownRegression(child);\n        DearustResources::CpuTimer invalid(child.pid,-1,{},0);')
    timer_source = output / 'timer.cpp'
    timer_source.write_text(timer)
    state = transformed['resource-server-state.inc']
    begin = state.index('struct ResourceState {')
    end = state.index('\nnamespace {\nclass ResourceGuard', begin)
    body = state[begin:end]
    begin = state.index('void Process::resourceDying() noexcept {')
    end = state.index('\nvoid Process::resourceExec()', begin)
    dying = state[begin:end]
    begin = state.index('        [](ResourceState* state) noexcept {')
    end = state.index('\n    const auto eid = _eid;', begin)
    deleter = state[begin:end]
    fixture = (tools / 'fixtures/resource-state-death-native.cpp.in').read_text()
    fixture = fixture.replace('@RESOURCE_STATE@', body).replace('@RESOURCE_DYING@', dying).replace('@RESOURCE_DELETER@', deleter)
    state_source = output / 'state.cpp'
    state_source.write_text(fixture)
    records = []
    for variant in (('native', 'sanitized') if args.sanitizers else ('native',)):
        flags = ['-O2'] if variant == 'native' else [
            '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        for name, source in [('state', state_source), ('timer', timer_source)]:
            binary = output / (name + '-' + variant)
            command = ['clang++', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror',
                       '-pthread', '-I' + str(output), str(source), '-o', str(binary)]
            if name == 'timer':
                command += ['-Wl,--wrap=timer_create', '-Wl,--wrap=timer_settime']
            for execution in (command, [str(binary)]):
                env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:alloc_dealloc_mismatch=1')
                result = subprocess.run(execution, text=True, capture_output=True, env=env, timeout=90)
                records.append(dict(variant=variant, fixture=name, command=execution,
                                    exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
                if result.returncode:
                    break
            if records[-1]['exit_code']:
                break
        if records[-1]['exit_code']:
            break
    unchanged = all(digest(Path(name)) == expected for name, expected in hashes.items())
    report = dict(passed=unchanged and all(record['exit_code'] == 0 for record in records),
                  inputs_unchanged=unchanged, source_sha256=hashes, commands=records,
                  generated_sha256={str(path): digest(path) for path in (timer_source, state_source, output / 'resource-cpu-timer.inc', output / 'resource-server-state.inc')},
                  scope=__doc__)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
