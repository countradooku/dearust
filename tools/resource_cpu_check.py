#!/usr/bin/env python3
"""Validate the standalone precreated CPU receiver and reproduce glibc callback loss.

No resource ownership, server installation, RPC, exec, Checkin, continuation or
C wait-admission checks are performed. The original preserved helper is read
only; its actual SIGEV_THREAD expiry allocation is failed in an owned process.
"""
import argparse
import hashlib
import json
import os
import signal
from pathlib import Path
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
    files = [root/name for name in ('resource_cpu_check.py', 'resource_cpu_dispatch.inc',
             'resource_cpu_dispatch_native.cpp', 'resource_cpu_original_loss_native.cpp',
             'resource-cpu-timer.inc')]
    initial = {str(path): digest(path) for path in files}
    records = []
    def checkpoint():
        (output/'partial.json').write_text(json.dumps(dict(source_sha256=initial, records=records), indent=2)+'\n')

    for variant in ('original-loss', 'native', 'raw-native', 'asan-ubsan'):
        binary = output/variant
        fixture = root/('resource_cpu_original_loss_native.cpp' if variant == 'original-loss'
                        else 'resource_cpu_dispatch_native.cpp')
        flags = ['-O2'] if variant != 'asan-ubsan' else [
            '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-fno-omit-frame-pointer', '-DCPU_NO_MALLOC_INTERPOSE']
        if variant == 'raw-native':
            flags += ['-DCPU_NO_SIGNAL_ADAPTER']
        wraps = [] if variant == 'original-loss' else [
            '-Wl,--wrap='+name for name in ('pthread_create', 'timer_create',
                                           'timer_settime', 'timer_gettime', 'timer_delete', 'signalfd', 'eventfd')]
        command = ['clang++', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror',
                   '-pthread', *wraps, str(fixture), '-o', str(binary)]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=120)
        records.append(dict(variant=variant, phase='compile', command=command,
                            exit_code=compiled.returncode, stdout=compiled.stdout,
                            stderr=compiled.stderr))
        checkpoint()
        if compiled.returncode:
            break
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1', UBSAN_OPTIONS='halt_on_error=1')
        timed_out = False
        stdout_path, stderr_path = output/(variant+'.stdout'), output/(variant+'.stderr')
        with stdout_path.open('w') as stdout, stderr_path.open('w') as stderr:
            process = subprocess.Popen([str(binary)], stdout=stdout, stderr=stderr,
                                       cwd=output, env=env, start_new_session=True)
            try:
                code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
        records.append(dict(variant=variant, phase='run', command=[str(binary)],
                            binary_sha256=digest(binary), exit_code=code, timed_out=timed_out,
                            stdout=stdout_path.read_text(), stderr=stderr_path.read_text()))
        checkpoint()
        if code or timed_out:
            break
    unchanged = initial == {str(path): digest(path) for path in files}
    passed = unchanged and len(records) == 8 and all(row['exit_code'] == 0 for row in records)
    report = dict(scope=__doc__, passed=passed, inputs_unchanged=unchanged,
                  source_sha256=initial, records=records,
                  compiler=subprocess.check_output(['clang++', '--version'], text=True),
                  installed_libc_sha256=digest(Path('/lib64/libc.so.6')),
                  caveats=['Original-loss case interposes the actual installed glibc callback-data malloc; no timer callback is mocked.',
                           'Allocation denial applies to native current execution; ASan/UBSan use their own allocator and instrument C++ only.',
                           'Caller must exclusively reserve a non-ignored RT signal and preserve that disposition.',
                           'No production activation, whole-runtime OOM or performance acceptance is claimed.'])
    (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(passed=passed, inputs_unchanged=unchanged, records=len(records))))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
