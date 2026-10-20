#!/usr/bin/env python3
"""Exercise unanchored binary SIMD copies without RPC or resource activation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = Path(__file__).resolve().parent/'fixtures/resource-copy-simd-native.c'
    objects = args.runtime.resolve()/'objects'
    manifest = args.runtime.resolve()/'darling-runtime-artifacts.tsv'
    paths_row = [row for row in manifest.read_text().splitlines()
                 if not row.startswith('#') and row.split('\t')[5] == 'objects/paths.o']
    if len(paths_row) != 1 or paths_row[0].split('\t')[2:4] != ['dearust-path', 'runtime']:
        raise ValueError('runtime manifest must select exactly the production paths object')
    guarded = Path(__file__).resolve().parents[1]/'crates/dearust-path/src/abi/guarded.rs'
    inputs = [fixture, Path(__file__).resolve(), guarded, manifest,
              objects/'paths.o', objects/'errno.o']
    initial = {str(path): digest(path) for path in inputs}
    records = []
    for name, flags in [('native', []), ('asan-ubsan', ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'])]:
        binary = output/name
        command = ['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-Wl,--gc-sections', *flags,
                   str(fixture), str(objects/'paths.o'), str(objects/'errno.o'), '-o', str(binary)]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:handle_segv=0:handle_sigbus=0', UBSAN_OPTIONS='halt_on_error=1')
        result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, env=env, timeout=30)
        records.append(dict(name=name, command=command, binary_sha256=digest(binary), stdout=result.stdout, stderr=result.stderr))
    if initial != {str(path): digest(path) for path in inputs}:
        raise ValueError('source, staged manifest or objects changed during validation')
    report = dict(scope=__doc__, initial_sha256=initial, inputs_unchanged=True,
                  staged_paths_manifest_row=paths_row[0],
                  source_sha256={str(path): initial[str(path)] for path in (fixture, Path(__file__).resolve(), guarded)},
                  object_sha256={name: digest(objects/name) for name in ('paths.o', 'errno.o')}, records=records,
                  caveats=['Native signal/ABI adapter only; Rust and assembly are not sanitizer instrumented.', 'No blocked RPC, Checkin, exec-notification, continuation-lifetime or C queue-admission checks are run.'])
    (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
