#!/usr/bin/env python3
"""Check CPU timer observation failure and same-binding recovery in owned processes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    files = [root/name for name in ('resource_cpu_query_check.py', 'resource_cpu_query_failure_native.cpp',
                                   'resource_cpu_dispatch_native.cpp', 'resource_cpu_dispatch.inc')]
    initial = {str(path): digest(path) for path in files}
    records = []
    for variant in ('native', 'asan-ubsan'):
        binary = output/variant
        flags = ['-O2'] if variant == 'native' else [
            '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-omit-frame-pointer', '-DCPU_NO_MALLOC_INTERPOSE']
        wraps = ['-Wl,--wrap='+name for name in ('pthread_create', 'timer_create', 'timer_settime',
                                                'timer_gettime', 'timer_delete', 'signalfd', 'eventfd')]
        command = ['clang++', '-std=c++17', *flags, '-DCPU_NO_SIGNAL_ADAPTER', '-Wall', '-Wextra',
                   '-Werror', '-pthread', *wraps, str(files[1]), '-o', str(binary)]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=120)
        records.append(dict(variant=variant, phase='compile', command=command, exit_code=compiled.returncode,
                            stdout=compiled.stdout, stderr=compiled.stderr))
        if compiled.returncode:
            break
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1', UBSAN_OPTIONS='halt_on_error=1')
        stdout_path, stderr_path = output/(variant+'.stdout'), output/(variant+'.stderr')
        with stdout_path.open('w') as stdout, stderr_path.open('w') as stderr:
            process = subprocess.Popen([str(binary)], stdout=stdout, stderr=stderr,
                                       cwd=output, env=env, start_new_session=True)
            try:
                code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
        records.append(dict(variant=variant, phase='run', binary_sha256=digest(binary), exit_code=code,
                            stdout=stdout_path.read_text(), stderr=stderr_path.read_text()))
        if code:
            break
    unchanged = initial == {str(path): digest(path) for path in files}
    passed = unchanged and len(records) == 4 and all(row['exit_code'] == 0 for row in records)
    report = dict(scope=__doc__, source_sha256=initial, records=records, inputs_unchanged=unchanged,
                  passed=passed, resource_ownership_activated=False, blocked_checks_retried=False,
                  caveats=['Only timer_gettime failure is injected; owned timers, CPU time, pidfds and delivery are real.',
                           'No RPC, exec, Checkin, continuation or wait-admission tests.',
                           'C++ helper/adapters are sanitizer instrumented; libc/kernel are not.'])
    (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(passed=passed, inputs_unchanged=unchanged)))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
