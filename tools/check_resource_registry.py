#!/usr/bin/env python3
"""Test the exact owned Registry header with ordinary native allocation failures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_resource_registry import patch_registry


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='darlingserver source directory')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    source = args.source.resolve(strict=True) / 'internal-include/darlingserver/registry.hpp'
    fixture = root / 'fixtures/resource-registry-native.cpp'
    inputs = [source, fixture, root / 'patch_resource_registry.py', Path(__file__).resolve()]
    hashes = {str(p): digest(p) for p in inputs}
    include = output / 'include/darlingserver'
    include.mkdir(parents=True)
    # Header-only template testing uses empty dependency headers; no server/RPC emulation.
    for name in ('message.hpp', 'process.hpp', 'thread.hpp'):
        (include / name).write_text('#pragma once\nnamespace DarlingServer { class Process; class Thread; }\n')
    header = include / 'registry.hpp'
    header.write_text(patch_registry(source.read_text()))
    records = []
    for variant in (('native', 'sanitized') if args.sanitizers else ('native',)):
        binary = output / variant
        flags = ['-O2'] if variant == 'native' else ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        commands = [['clang++', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror', '-pthread', '-I'+str(include.parent), str(fixture), '-o', str(binary)], [str(binary)]]
        for command in commands:
            env = os.environ.copy()
            env['ASAN_OPTIONS'] = 'detect_leaks=1:alloc_dealloc_mismatch=1'
            result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
            records.append(dict(variant=variant, command=command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
            if result.returncode:
                break
        if records[-1]['exit_code']:
            break
    unchanged = all(digest(Path(name)) == expected for name, expected in hashes.items())
    report = dict(passed=unchanged and all(r['exit_code'] == 0 for r in records), source_sha256=hashes,
                  owned_header_sha256=digest(header), commands=records, inputs_unchanged=unchanged,
                  scope='native header template only; no runtime lifecycle, RPC or resource enforcement claim')
    (output / 'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
