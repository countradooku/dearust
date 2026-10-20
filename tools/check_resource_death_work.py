#!/usr/bin/env python3
"""Exercise the exact transformed worker template with real native threads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from patch_resource_death_work import patch_workers


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tools = Path(__file__).resolve().parent
    source = args.source.resolve() / 'internal-include/darlingserver/workers.hpp'
    fixture = tools / 'fixtures/resource-death-work-native.cpp'
    inputs = [source, fixture, tools / 'patch_resource_death_work.py', Path(__file__).resolve()]
    initial = {str(path): digest(path) for path in inputs}
    include = output / 'include/darlingserver'
    include.mkdir(parents=True)
    (include / 'config.hpp').write_text('''#pragma once
namespace DarlingServer { struct Config { static constexpr bool singleThreadedWorkQueue = true; }; }
''')
    text = patch_workers(source.read_text())
    # A test-only barrier makes queued shutdown deterministic; all queue code
    # surrounding it is the production transformed implementation.
    text = 'extern void reservedTestDying() noexcept;\n' + text.replace(
        '\t\t\t_dying = true;', '\t\t\t_dying = true; reservedTestDying();')
    header = include / 'workers.hpp'
    header.write_text(text)
    records = []
    for variant in ('native', 'sanitized'):
        flags = ['-O2'] if variant == 'native' else ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        binary = output / variant
        for command in [
            ['clang++', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror', *flags,
             '-I'+str(include.parent), str(fixture), '-o', str(binary)], [str(binary)]]:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                    env=dict(os.environ, ASAN_OPTIONS='detect_leaks=1'))
            records.append(dict(variant=variant, command=command, exit_code=result.returncode,
                                stdout=result.stdout, stderr=result.stderr))
            if result.returncode:
                break
        if records[-1]['exit_code']:
            break
    unchanged = all(digest(path) == value for path, value in initial.items())
    report = dict(passed=unchanged and all(r['exit_code'] == 0 for r in records),
                  scope='Exact reserved worker template; real pthreads and modeled Thread item bodies. No dtape/guest/RPC or whole active-call OOM claim.',
                  input_sha256=initial, inputs_unchanged=unchanged,
                  owned_header_sha256=digest(header), commands=records)
    (output / 'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(passed=report['passed'], report=str(output/'verification.json'))))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
