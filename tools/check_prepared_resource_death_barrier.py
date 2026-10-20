#!/usr/bin/env python3
"""Check exact transformed death/worker decisions with deterministic interleaving."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from patch_prepared_resource_death_barrier import patch_thread_death_barrier


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', type=Path, required=True, help='owned prepared server build before barrier transform')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    server = args.server.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    fixture = root / 'fixtures/prepared-resource-death-barrier-native.cpp.in'
    header = server / 'internal-include/darlingserver/thread.hpp'
    source = server / 'src/thread.cpp'
    inputs = {str(path): digest(path) for path in [fixture, header, source,
              root / 'patch_prepared_resource_death_barrier.py', Path(__file__).resolve()]}
    transformed = patch_thread_death_barrier({'thread.hpp': header.read_text(), 'thread.cpp': source.read_text()})['thread.cpp']
    begin = transformed.index('void DarlingServer::Thread::notifyDead() {')
    end = transformed.index('bool DarlingServer::Thread::isDead() const', begin)
    notify = transformed[begin:end]
    begin = transformed.index('void DarlingServer::Thread::abortKernelHandles() noexcept {')
    end = transformed.index('void DarlingServer::Thread::_resourceCleanupRun(', begin)
    abort = transformed[begin:end]
    begin = transformed.index('\tbool canRelease = false;', transformed.index('doneWorking:'))
    end = transformed.index('\tif (_terminating && !_dead) {', begin)
    decision = transformed[begin:end]
    selected = output / 'prepared-resource-death-barrier-native.cpp'
    selected.write_text(fixture.read_text().replace('// NOTIFY_DEAD', notify)
                        .replace('// ABORT_KERNEL_HANDLES', abort)
                        .replace('// WORKER_RELEASE_DECISION', decision))
    records = []
    for variant in (('native', 'sanitized') if args.sanitizers else ('native',)):
        binary = output / variant
        flags = ['-O2'] if variant == 'native' else ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        for command in [['clang++', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror', '-pthread', str(selected), '-o', str(binary)], [str(binary)]]:
            env = os.environ.copy()
            env['ASAN_OPTIONS'] = 'detect_leaks=1:alloc_dealloc_mismatch=1'
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
            records.append(dict(variant=variant, command=command, exit_code=result.returncode,
                                stdout=result.stdout, stderr=result.stderr))
            if result.returncode:
                break
        if records[-1]['exit_code']:
            break
    unchanged = all(digest(Path(path)) == value for path, value in inputs.items())
    report = dict(passed=unchanged and all(item['exit_code'] == 0 for item in records),
                  input_sha256=inputs, owned_source_sha256=digest(selected),
                  inputs_unchanged=unchanged, commands=records,
                  scope='exact transformed notifyDead/abort/worker release decision with controlled dying adapter; full published-death notification/OOM, dtape internals, RPC and guest gates remain separate')
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
