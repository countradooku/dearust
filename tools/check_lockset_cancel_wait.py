#!/usr/bin/env python3
"""Exact selected C cancellation/queue-first functions with native modeled locks."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def function(text, signature):
    start = text.index(signature)
    body = text.index('{', start)
    depth = 1
    end = body + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--xnu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    paths = [Path('crates/dearust-lockset/bridge/sync_lock.c'), a.source / 'duct-tape/src/thread.c', a.xnu / 'osfmk/kern/waitq.c', Path('tools/fixtures/lockset-cancel-wait-native.cpp'), Path(__file__)]
    hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    bridge, thread, waitq, fixture, _ = [p.read_text() for p in paths]
    functions = [
        function(waitq, 'static kern_return_t\nwaitq_select_thread_locked('),
        function(waitq, 'kern_return_t\nwaitq_wakeup64_thread('),
        function(waitq, 'int\nwaitq_pull_thread_locked('),
        function(thread, 'kern_return_t\nthread_wakeup_thread('),
        function(thread, 'clear_wait_internal(\n'),
        function(bridge, 'int darling_lockset_cancel_wait('),
    ]
    # clear_wait_internal return type appears immediately before its signature.
    functions[4] = 'kern_return_t\n' + functions[4]
    source = a.output / 'native.cpp'
    source.write_text(fixture.replace('// @@EXACT_FUNCTIONS@@', '\n\n'.join(functions)))
    records = []
    for suffix, extra in [('native', []), ('sanitized', ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'])]:
        binary = a.output / suffix
        for command in [ ['clang++', '-std=c++17', '-O1', '-g', '-pthread', *extra, str(source), '-o', str(binary)], [str(binary.resolve())] ]:
            r = subprocess.run(command, text=True, capture_output=True, timeout=60)
            records.append(dict(command=command, exit_code=r.returncode, stdout=r.stdout, stderr=r.stderr))
            (a.output / 'verification.json').write_text(json.dumps(dict(passed=False, input_sha256=hashes, commands=records), indent=2)+'\n')
            if r.returncode:
                raise SystemExit(r.stderr or r.stdout)
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p,h in hashes.items())
    (a.output / 'verification.json').write_text(json.dumps(dict(passed=unchanged, inputs_unchanged=unchanged, input_sha256=hashes, commands=records, scope='Exact bridge cancellation, XNU targeted wake/selection and old clear_wait retry functions; native mutex/scheduler/list models. Global event queue case only, no RPC or guest/whole scheduler proof.'), indent=2)+'\n')
    print(a.output / 'verification.json')


if __name__ == '__main__':
    main()
