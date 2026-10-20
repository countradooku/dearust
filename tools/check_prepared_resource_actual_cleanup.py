#!/usr/bin/env python3
"""Build/run a fresh native server derivative with actual dtape lifecycle hooks.

No guest bootstrap, RPC or received messages. Failure hooks throw after successful
real acquisitions/publication preparation; they do not simulate inner-XNU malloc
failure or establish allocation-exhaustion safety of all C teardown internals.
Public glibc allocation entry points are denied on the cleanup lane after startup;
hidden direct libc-internal allocations are outside this interposition scope.
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
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--internal-build', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    output = args.output.resolve()
    if args.internal_build:
        import build_prepared_resource_server as builder
        from patch_prepared_resource_actual_cleanup import patch_actual_cleanup_validation
        production = builder.patch_registration
        native_main = (candidate / 'src/darlingserver.cpp').read_text()
        builder.patch_registration = lambda files: patch_actual_cleanup_validation(production(files), native_main)
        sys.argv = [str(Path(builder.__file__).resolve()), '--candidate', str(candidate), '--output', str(output)]
        builder.main()
        return
    output.mkdir(parents=True, exist_ok=False)
    tools = Path(__file__).resolve().parent
    sources = [Path(__file__).resolve(), tools / 'patch_prepared_resource_actual_cleanup.py',
               tools / 'prepared-resource-actual-cleanup.inc',
               tools / 'prepared-resource-actual-allocations.inc']
    initial_hooks = {str(path): digest(path) for path in sources}
    original_report = json.loads((candidate / 'build.json').read_text())
    preserved = {name: digest(Path(name)) for name in original_report['sha256']}
    build_command = [sys.executable, str(Path(__file__).resolve()), '--candidate', str(candidate),
                     '--output', str(output / 'candidate'), '--internal-build']
    built = subprocess.run(build_command, text=True, capture_output=True)
    (output / 'build.log').write_text(built.stdout + built.stderr)
    built.check_returncode()
    prefix = output / 'native-prefix'
    prefix.mkdir()
    binary = output / 'candidate/linked/darlingserver'
    environment = dict(os.environ)
    environment['DSERVER_LOG_LEVEL'] = 'error'
    # sockaddr_un paths are short; keep the owned prefix relative to the owned
    # working directory instead of silently truncating an absolute workspace path.
    result = subprocess.run([str(binary), prefix.name], text=True, capture_output=True,
                            env=environment, timeout=40, cwd=output)
    (output / 'native.log').write_text(result.stdout + result.stderr)
    result.check_returncode()
    marker = 'DEARUST_ACTUAL_CLEANUP_RESULT '
    data = json.loads(next(line[len(marker):] for line in result.stdout.splitlines() if line.startswith(marker)))
    for name, expected in preserved.items():
        if digest(Path(name)) != expected:
            raise ValueError('preserved candidate input changed: ' + name)
    for name, expected in initial_hooks.items():
        if digest(Path(name)) != expected:
            raise ValueError('test hook input changed while validating: ' + name)
    record = dict(scope=__doc__, command=build_command, result=data,
                  server=str(binary), server_sha256=digest(binary),
                  preserved_inputs=len(preserved),
                  sha256=initial_hooks, hooks_frozen_before_build=True)
    (output / 'verification.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
