#!/usr/bin/env python3
"""Real XNU port/mqueue callback and detach validation before accepting calls.

No guest bootstrap or received RPCs. Native hooks pause the production callback
after its atomic snapshot, and after C++ weak promotion, while real kernel-lane
detach contends on the actual XNU mqueue lock. Public allocation ABIs are denied
on the retiring caller and cleanup lane; libc-hidden allocations are unobserved.
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
    parser.add_argument('--candidate', required=True, type=Path, help='frozen resource server12 parent')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--internal-build', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    output = args.output.resolve()
    tools = Path(__file__).resolve().parent
    hooks = [Path(__file__).resolve(), tools / 'patch_prepared_resource_actual_machport.py',
             tools / 'prepared-resource-actual-machport.inc', tools / 'prepared-resource-actual-machport.c.inc',
             tools / 'prepared-resource-actual-allocations.inc',
             tools / 'patch_prepared_resource_kq_ownership.py',
             tools / 'patch_prepared_resource_mach_notifications.py',
             tools / 'prepared-resource-mach-notifications.inc',
             tools / 'patch_prepared_resource_mach_waiter.py']
    if args.internal_build:
        import build_prepared_resource_server as builder
        from build_resource_lifecycle_server import configure
        from patch_prepared_resource_actual_machport import patch_actual_machport_validation, instrument_actual_machport_c
        configure(builder, candidate, output, instrument_c=instrument_actual_machport_c)
        production = builder.patch_checkin
        native_main = (candidate / 'src/darlingserver.cpp').read_text()
        builder.patch_checkin = lambda files: patch_actual_machport_validation(production(files), native_main)
        builder.EXTRA_TRANSFORM_INPUTS += hooks
        sys.argv = [str(Path(builder.__file__).resolve()), '--candidate', str(candidate), '--output', str(output)]
        builder.main()
        return
    output.mkdir(parents=True, exist_ok=False)
    initial_hooks = {str(path): digest(path) for path in hooks}
    base = json.loads((candidate / 'build.json').read_text())
    preserved = {path: digest(Path(path)) for path in base['sha256']}
    command = [sys.executable, str(Path(__file__).resolve()), '--candidate', str(candidate),
               '--output', str(output / 'candidate'), '--internal-build']
    result = subprocess.run(command, text=True, capture_output=True)
    (output / 'build.log').write_text(result.stdout + result.stderr)
    result.check_returncode()
    prefix = output / 'native-prefix'; prefix.mkdir()
    binary = output / 'candidate/linked/darlingserver'
    environment = dict(os.environ); environment['DSERVER_LOG_LEVEL'] = 'error'
    run_command = [str(binary), prefix.name]
    result = subprocess.run(run_command, text=True, capture_output=True, timeout=60, cwd=output, env=environment)
    (output / 'native.log').write_text(result.stdout + result.stderr)
    result.check_returncode()
    marker = 'DEARUST_ACTUAL_MACHPORT_RESULT '
    data = json.loads(next(line[len(marker):] for line in result.stdout.splitlines() if line.startswith(marker)))
    unchanged = all(digest(Path(path)) == expected for path, expected in {**preserved, **initial_hooks}.items())
    report = dict(passed=unchanged and bool(data), scope=__doc__, build_command=command,
                  run_command=run_command, result=data, inputs_unchanged=unchanged,
                  server=str(binary), server_sha256=digest(binary), preserved_inputs=len(preserved),
                  hook_sha256=initial_hooks)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
