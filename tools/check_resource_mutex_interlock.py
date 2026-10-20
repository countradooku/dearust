#!/usr/bin/env python3
"""Exact selected mutex functions/types and real libsimple native interlock.

Current-thread identity and suspend/resume callbacks are modeled. This does not
exercise RPC, a server kernel context, or ucontext migration. The no-active
blocking busy-spin fallback remains unchanged and is not a performance claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from patch_resource_mutex_interlock import patch_mutex_interlock, patch_mutex_misc

RECORD = None


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, records):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60,
        env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1'})
    records.append(dict(command=command, exit_code=result.returncode,
                        stdout=result.stdout, stderr=result.stderr))
    RECORD.write_text(json.dumps(records, indent=2) + '\n')
    result.check_returncode()
    return result


def function(source, signature):
    begin = source.index(signature)
    return source[begin:source.index('\n};', begin) + len('\n};')]


def mutex_functions(source):
    begin = source.find('// The queue interlock orders ownership transitions.')
    if begin < 0: begin = source.index('void dtape_mutex_init(')
    return source[begin:source.index('void lck_mtx_init(', begin)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Selected Darlingserver source directory')
    parser.add_argument('--baseline', required=True, type=Path, help='Committed original dependency repository')
    parser.add_argument('--libsimple', required=True, type=Path, help='Actual libsimple source directory')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    global RECORD
    RECORD = output / 'commands.json'
    tools = Path(__file__).resolve().parent
    root = args.source.resolve() / 'duct-tape'
    lock_source = root / 'src/locks.c'
    misc_source = root / 'src/misc.c'
    lock_type = root / 'internal-include/darlingserver/duct-tape/locks.h'
    libsimple = args.libsimple.resolve()
    fixture = tools / 'fixtures/resource-mutex-interlock-native.c.in'
    inputs = [lock_source, misc_source, lock_type, fixture, Path(__file__).resolve(),
              tools / 'patch_resource_mutex_interlock.py', libsimple / 'src/lock.c',
              libsimple / 'include/libsimple/lock.h', libsimple / 'include/libsimple/base.h']
    hashes = {str(path): digest(path) for path in inputs}
    pin = subprocess.check_output(['git', '-C', str(args.baseline.resolve()), 'rev-parse', 'HEAD'], text=True).strip()
    original = subprocess.check_output(['git', '-C', str(args.baseline.resolve()), 'show', pin + ':duct-tape/src/locks.c'], text=True)
    selected, misc = lock_source.read_text(), misc_source.read_text()
    if mutex_functions(original) != mutex_functions(selected):
        raise ValueError('selected original mutex functions differ from pinned dependency')
    generated = patch_mutex_interlock(selected)
    generated_misc = patch_mutex_misc(misc)
    (output / 'original-pinned-locks.c').write_text(original)
    (output / 'selected-locks.c').write_text(selected)
    (output / 'generated-locks.c').write_text(generated)
    (output / 'generated-misc.c').write_text(generated_misc)
    include = output / 'include/darlingserver/duct-tape'
    include.mkdir(parents=True)
    (include / 'locks.h').write_text(lock_type.read_text())
    records = []
    results = {}
    for variant, flags in [('native', ['-O2']), ('sanitized', ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all'])]:
        for label, locks, diagnostics in [('before', selected, misc), ('after', generated, generated_misc)]:
            source = fixture.read_text().replace('@MUTEX@', mutex_functions(locks))
            source = source.replace('@DESTROY@', function(locks, 'void lck_mtx_destroy('))
            source = source.replace('@WAITQ@', function(diagnostics, 'unsigned int waitq_held('))
            path = output / (variant + '-' + label + '.c')
            path.write_text(source)
            binary = output / (variant + '-' + label)
            command = ['clang', '-std=gnu11', '-pthread', '-DLIBSIMPLE_LINUX=1', *flags]
            if label == 'before': command.append('-DOLD_MUTEX_BASELINE')
            command += ['-I' + str(include.parents[1]), '-I' + str(libsimple / 'include'),
                str(path), str(libsimple / 'src/lock.c'), '-o', str(binary)]
            run(command, records)
            execution = run([str(binary)], records)
            results[variant + '-' + label] = json.loads(execution.stdout)
    unchanged = all(digest(path) == expected for path, expected in hashes.items())
    if not unchanged: raise ValueError('mutex proof input changed')
    report = dict(passed=True, scope=__doc__, inputs_unchanged=unchanged, input_sha256=hashes,
        pinned_original=dict(repository=str(args.baseline.resolve()), commit=pin,
            selected_mutex_functions_exact=True), results=results,
        owner_access_inventory=['src/locks.c: every owner store/read now uses relaxed atomic builtins',
            'src/misc.c: waitq_held diagnostic now uses relaxed atomic load',
            'volatile uintptr_t type and layout unchanged; queue interlock provides synchronization'],
        adjustments=['exact internal mutex type/header with host sys/queue.h TAILQ macros',
            'modeled current-thread identity and blocking native condition-variable suspend/resume callbacks',
            'full actual libsimple/src/lock.c compiled with LIBSIMPLE_LINUX=1'], commands=records)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, results=results)))


if __name__ == '__main__': main()
