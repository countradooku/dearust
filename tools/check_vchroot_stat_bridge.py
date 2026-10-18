#!/usr/bin/env python3
"""Differentially execute original and bridged vchroot units on owned Linux files."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from build_vchroot_stat_bridge import BASE, INPUT, patch_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(source):
        parser.error('output must be outside source')
    raw = (source / INPUT).read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    include = output / 'include/darling'
    include.mkdir(parents=True)
    (include / 'emulation').symlink_to(source / BASE / 'include', target_is_directory=True)
    # Original upstream standalone TEST configuration only needs spelling
    # adapters for these unused Darwin helpers and native locale operation.
    prelude = '''#define _GNU_SOURCE
#include <stdint.h>
#include <strings.h>
#define LC_C_LOCALE 0
#define strncasecmp_l(a,b,c,l) strncasecmp(a,b,c)
int dserver_rpc_vchroot_path(char*,unsigned long,uint64_t*);
int dserver_rpc_vchroot(int);
#define __simple_abort() __builtin_trap()
#define __simple_printf(...) ((void)0)
'''
    helper = Path(__file__).resolve().parent / 'fixtures/vchroot-at-bridge.h'
    (output / 'vchroot-at-bridge.h').write_bytes(helper.read_bytes())
    common = ['clang', '-O2', '-g']
    if args.sanitize:
        common += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    records = []

    def run(command):
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        records.append({'command': command, 'exit': result.returncode, 'output': result.stdout})
        if result.returncode:
            (output / 'failure.json').write_text(json.dumps(records, indent=2) + '\n')
        result.check_returncode()
        return result.stdout

    for name, text in [('original', raw.decode()), ('bridge', patch_source(raw.decode()))]:
        copied = output / (name + '.c')
        copied.write_text(prelude + text + ('\n#include "vchroot-at-bridge.h"\n' if name == 'bridge' else ''))
        command = [*common, '-DTEST', '-Dmain=' + name + '_unused_main',
                   '-D__darling_vchroot=' + name + '_root',
                   '-Dvchroot_expand=' + name + '_expand',
                   '-Dvchroot_fdpath=' + name + '_fdpath',
                   '-Dvchroot_unexpand=' + name + '_unexpand',
                   '-I', str(output / 'include'), '-c', str(copied),
                   '-o', str(output / (name + '.o'))]
        run(command)
    fixture = Path(__file__).resolve().parent / 'fixtures/vchroot-stat-native.c'
    run([*common, str(fixture), str(output / 'original.o'), str(output / 'bridge.o'),
         '-o', str(output / 'differential')])
    stdout = run([str(output / 'differential')])
    bounds = Path(__file__).resolve().parent / 'fixtures/vchroot-bounds-native.c'
    run([*common, str(bounds), str(output / 'original.o'), str(output / 'bridge.o'),
         '-o', str(output / 'bounds')])
    for case in ('append', 'exit', 'unexpand', 'fdzero', 'fdroot', 'filedots',
                 'pipedirfd', 'symlinklong'):
        run([str(output / 'bounds'), 'bridge', case])
    at_fixture = Path(__file__).resolve().parent / 'fixtures/vchroot-at-native.c'
    run([*common, '-Wall', '-Wextra', '-Werror', str(at_fixture),
         '-o', str(output / 'descriptor-relative')])
    run([str(output / 'descriptor-relative')])
    root_fixture = Path(__file__).resolve().parent / 'fixtures/vchroot-root-native.c'
    run([*common, '-DTEST', '-DDEARUST_TEST_ROOT_RPC', '-Derrno_linux_to_bsd(x)=(x)',
         '-Dmain=rpc_unused_main', '-D__darling_vchroot=rpc_root',
         '-Dvchroot_expand=rpc_expand', '-Dvchroot_fdpath=rpc_fdpath',
         '-Dvchroot_unexpand=rpc_unexpand', '-I', str(output / 'include'),
         '-c', str(output / 'bridge.c'), '-o', str(output / 'root-rpc.o')])
    run([*common, '-Wall', '-Wextra', '-Werror', str(root_fixture),
         str(output / 'root-rpc.o'), '-o', str(output / 'root-transaction')])
    run([str(output / 'root-transaction')])
    (output / 'verification.json').write_text(json.dumps({
        'source': str(source / INPUT), 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
        'root_fixture_sha256': hashlib.sha256(root_fixture.read_bytes()).hexdigest(),
        'bounds_fixture_sha256': hashlib.sha256(bounds.read_bytes()).hexdigest(),
        'at_fixture_sha256': hashlib.sha256(at_fixture.read_bytes()).hexdigest(),
        'at_helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
        'sanitizers': args.sanitize, 'commands': records,
        'scope': 'Native TEST resolver logic, real owned Linux files; guest runtime execution is separate',
    }, indent=2) + '\n')
    print(stdout, end='')


if __name__ == '__main__':
    main()
