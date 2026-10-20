#!/usr/bin/env python3
"""Exercise exact owned Message/MessageQueue transport under allocator denial."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from patch_prepared_resource_replies import patch_prepared_replies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepared, source, output = args.prepared.resolve(strict=True), args.source.resolve(strict=True), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    paths = {'message.hpp': source/'internal-include/darlingserver/message.hpp',
             'message.cpp': prepared/'src/message.cpp',
             'rpc.internal.h': prepared/'internal-include/darlingserver/rpc.internal.h',
             'call.cpp': prepared/'src/call.cpp'}
    inputs = [*paths.values(), Path(__file__).resolve(), root/'patch_prepared_resource_replies.py',
              root/'prepared-resource-reply-queue.inc', root/'prepared-resource-reply-send.inc',
              root/'fixtures/prepared-resource-replies-native.cpp']
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    initial = {str(p): digest(p) for p in inputs}
    patched = patch_prepared_replies({key: path.read_text() for key, path in paths.items()})
    for name, text in patched.items():
        path = output / ('include/darlingserver' if name.endswith('.hpp') else 'src') / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    fixture = output/'fixture.cpp'
    fixture.write_text((root/'fixtures/prepared-resource-replies-native.cpp').read_text())
    records = []
    for variant, flags in [('native', ['-O2']), ('sanitized', ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all'])]:
        binary = output/variant
        command = ['clang++', '-D_GNU_SOURCE', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror',
                   '-Wno-address-of-temporary', *flags, '-I', str(output/'include'), str(output/'src/message.cpp'),
                   str(fixture), '-Wl,--wrap=malloc', '-Wl,--wrap=calloc', '-Wl,--wrap=realloc', '-o', str(binary)]
        for cmd in [command, [str(binary)]]:
            env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:alloc_dealloc_mismatch=1')
            result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
            records.append(dict(command=cmd, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
            if result.returncode: break
        if records[-1]['exit_code']: break
    report = dict(passed=all(r['exit_code'] == 0 for r in records) and all(digest(Path(p)) == h for p,h in initial.items()),
                  scope=__doc__ + ' Exact Message and queue source; native libc sockets, descriptors and C++ owners. This does not prove guest exec rollback or instrument linked Rust.',
                  input_sha256=initial, commands=records)
    (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(passed=report['passed'], report=str(output/'verification.json'))))
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
