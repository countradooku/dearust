#!/usr/bin/env python3
"""Validate actual owned cleanup adapters with native instrumented dtape handles.

The queue, preparation and release implementations are included verbatim from the
candidate helper inputs. The fixture does not execute real XNU or ucontext paths;
owned linked server and permitted guest checks are separate required evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tools = Path(__file__).resolve().parent
    fixture = tools / 'prepared-resource-cleanup-fixture.cpp'
    report = {'scope': 'ordinary native intrusive queue and actual preparation/release adapters; '
              'instrumented dtape handles and doWork; not inner-XNU-OOM or real-ucontext proof',
              'variants': {}}
    for variant, extra in [('native', []), ('sanitized', ['-fsanitize=address,undefined',
                                                        '-fno-omit-frame-pointer'])]:
        binary = output / variant
        command = ['clang++', '-std=c++17', '-O1', '-g', '-pthread', '-Wall', '-Wextra',
                   '-Werror', '-Wno-unused-function', '-I', str(tools), str(fixture),
                   '-Wl,--wrap=malloc', *extra, '-o', str(binary)]
        result = subprocess.run(command, text=True, capture_output=True)
        (output / (variant + '-compile.log')).write_text(result.stdout + result.stderr)
        result.check_returncode()
        environment = dict(os.environ)
        environment['ASAN_OPTIONS'] = 'detect_leaks=1'
        result = subprocess.run([str(binary)], text=True, capture_output=True,
                                timeout=60, env=environment)
        (output / (variant + '-run.log')).write_text(result.stdout + result.stderr)
        result.check_returncode()
        report['variants'][variant] = {'command': command,
                                      'ASAN_OPTIONS': environment['ASAN_OPTIONS'],
                                      'result': json.loads(result.stdout)}
    inputs = [Path(__file__).resolve(), fixture, tools / 'patch_prepared_resource_cleanup.py',
              tools / 'prepared-resource-cleanup.hpp',
              tools / 'prepared-resource-cleanup-thread.inc',
              tools / 'prepared-resource-cleanup-process.inc']
    report['sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
