#!/usr/bin/env python3
"""Validate actual owned ResourceState control-block failure and terminal death.

No RPC, received messages, guest execution or logical ownership activation.
Real native pidfds, POSIX CPU timers, dtape semaphores and the kernel cleanup lane
are used; allocation interposition retains its documented public-ABI scope.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--internal-build', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    candidate, output = args.candidate.resolve(strict=True), args.output.resolve()
    tools = Path(__file__).resolve().parent
    sources = [Path(__file__).resolve(), tools / 'patch_actual_resource_state_validation.py',
               tools / 'resource-state-actual-validation.inc',
               tools / 'patch_prepared_resource_actual_cleanup.py',
               tools / 'prepared-resource-actual-cleanup.inc', tools / 'prepared-resource-actual-allocations.inc']
    if args.internal_build:
        import build_prepared_resource_server as builder
        from build_resource_lifecycle_server import configure
        from patch_prepared_resource_actual_cleanup import patch_actual_cleanup_validation
        from patch_actual_resource_state_validation import patch_actual_resource_state_validation
        original_registration = builder.patch_registration
        native_main = (candidate / 'src/darlingserver.cpp').read_text()
        builder.patch_registration = lambda files: patch_actual_cleanup_validation(original_registration(files), native_main)
        def count_real_gate(sources):
            sources = dict(sources)
            sources['src/task.c'] += '''
unsigned long long dearust_actual_state_gate_count(void* raw) {
    task_t task = &((dtape_task_t*)raw)->xnu_task;
    task_lock(task);
    const unsigned long long count = task->semaphores_owned;
    task_unlock(task);
    return count;
}
'''
            return sources

        configure(builder, candidate, output, instrument_c=count_real_gate)
        final_patch = builder.patch_checkin
        builder.patch_checkin = lambda files: patch_actual_resource_state_validation(final_patch(files))
        builder.EXTRA_TRANSFORM_INPUTS += sources
        sys.argv = [str(Path(builder.__file__).resolve()), '--candidate', str(candidate), '--output', str(output)]
        builder.main()
        return
    output.mkdir(parents=True, exist_ok=False)
    hashes = {str(path): digest(path) for path in sources}
    base = json.loads((candidate / 'build.json').read_text())
    preserved = {name: digest(Path(name)) for name in base['sha256']}
    command = [sys.executable, str(Path(__file__).resolve()), '--candidate', str(candidate),
               '--output', str(output / 'candidate'), '--internal-build']
    build = subprocess.run(command, capture_output=True, text=True)
    (output / 'build.log').write_text(build.stdout + build.stderr)
    build.check_returncode()
    prefix = output / 'native-prefix'
    prefix.mkdir()
    binary = output / 'candidate/linked/darlingserver'
    result = subprocess.run([str(binary), prefix.name], cwd=output, capture_output=True,
                            text=True, timeout=45, env={**os.environ, 'DSERVER_LOG_LEVEL': 'error'})
    (output / 'native.log').write_text(result.stdout + result.stderr)
    result.check_returncode()
    results = {}
    for line in result.stdout.splitlines():
        for label, marker in [('state', 'DEARUST_ACTUAL_STATE_RESULT '),
                              ('lifecycle', 'DEARUST_ACTUAL_CLEANUP_RESULT ')]:
            if line.startswith(marker):
                results[label] = json.loads(line[len(marker):])
    if set(results) != {'state', 'lifecycle'}:
        raise ValueError('actual state or ordinary lifecycle result missing')
    if any(digest(Path(name)) != expected for name, expected in {**hashes, **preserved}.items()):
        raise ValueError('preserved validation or candidate input changed')
    record = dict(scope=__doc__, command=command, results=results,
                  sha256=hashes, preserved_candidate_inputs=len(preserved), inputs_unchanged=True,
                  server=str(binary), server_sha256=digest(binary))
    (output / 'verification.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
