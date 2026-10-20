#!/usr/bin/env python3
"""Exact Mach-port C/C++ preparation and weak-context retirement model checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from patch_prepared_resource_machport_retirement import patch_machport_retirement


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def section(text, begin, end):
    start = text.index(begin)
    return text[start:text.index(end, start)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    paths = {'kqchan.hpp': source / 'internal-include/darlingserver/kqchan.hpp',
             'kqchan.cpp': source / 'src/kqchan.cpp', 'kqchan.c': source / 'duct-tape/src/kqchan.c'}
    fixture_c = root / 'fixtures/prepared-resource-machport-model.c.in'
    fixture_cpp = root / 'fixtures/prepared-resource-machport-native.cpp.in'
    fixture_h = root / 'fixtures/prepared-resource-machport-model.h'
    cleanup = root / 'prepared-resource-cleanup.hpp'
    detach = source.parent / 'xnu/osfmk/ipc/ipc_pset.c'
    inputs = {str(path): digest(path) for path in [*paths.values(), fixture_c, fixture_cpp, fixture_h,
              cleanup, detach, root / 'patch_prepared_resource_machport_retirement.py', Path(__file__).resolve()]}
    transformed = patch_machport_retirement({name: path.read_text() for name, path in paths.items()})
    c_source = transformed['kqchan.c']; cpp_source = transformed['kqchan.cpp']
    c_text = fixture_c.read_text()
    substitutions = {
        '// CREATE': section(c_source, 'dtape_kqchan_mach_port_t* dtape_kqchan_mach_port_create(', 'void dtape_kqchan_mach_port_destroy('),
        '// DESTROY': section(c_source, 'void dtape_kqchan_mach_port_destroy(', 'void dtape_kqchan_mach_port_modify('),
        '// DISABLE': section(c_source, 'void dtape_kqchan_mach_port_disable_notifications(', 'bool dtape_kqchan_mach_port_fill('),
        '// POST': section(c_source, 'static void knote_post(', 'void knote(struct klist*'),
    }
    substitutions['// POST'] = substitutions['// POST'].replace('\tcb(ctx);', '\tprepared_snapshot_barrier();\n\tcb(ctx);')
    for key, value in substitutions.items(): c_text = c_text.replace(key, value)
    cpp_text = fixture_cpp.read_text()
    substitutions = {
        '@CLEANUP@': json.dumps(str(cleanup)),
        '// RETIREMENT_TYPE': section(cpp_source, 'struct DarlingServer::Kqchan::MachPort::PreparedRetirement {', 'DarlingServer::Kqchan::MachPort::MachPort('),
        '// MACH_DESTRUCTOR': section(cpp_source, 'DarlingServer::Kqchan::MachPort::~MachPort()', 'uintptr_t DarlingServer::Kqchan::MachPort::_idForProcess()'),
        '// SETUP': section(cpp_source, 'int DarlingServer::Kqchan::MachPort::setup()', 'void DarlingServer::Kqchan::MachPort::_processMessages()'),
    }
    for key, value in substitutions.items(): cpp_text = cpp_text.replace(key, value)
    selected_c = output / 'prepared-resource-machport-model.c'; selected_c.write_text(c_text)
    selected_cpp = output / 'prepared-resource-machport-native.cpp'; selected_cpp.write_text(cpp_text)
    (output / fixture_h.name).write_bytes(fixture_h.read_bytes())
    records = []
    for variant in (('native', 'sanitized') if args.sanitizers else ('native',)):
        flags = ['-O2'] if variant == 'native' else ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        obj = output / (variant + '.o'); binary = output / variant
        commands = [
            ['clang', '-std=gnu11', *flags, '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-pthread', '-c', str(selected_c), '-o', str(obj)],
            ['clang++', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror', '-pthread', str(selected_cpp), str(obj), '-o', str(binary)], [str(binary)],
        ]
        for command in commands:
            env = os.environ.copy(); env['ASAN_OPTIONS'] = 'detect_leaks=1:alloc_dealloc_mismatch=1'
            result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
            records.append(dict(variant=variant, command=command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
            if result.returncode: break
        if records[-1]['exit_code']: break
    unchanged = all(digest(Path(path)) == expected for path, expected in inputs.items())
    report = dict(passed=unchanged and all(item['exit_code'] == 0 for item in records), input_sha256=inputs,
                  generated_sha256={str(path): digest(path) for path in [selected_c, selected_cpp]},
                  inputs_unchanged=unchanged, commands=records,
                  scope='exact transformed C create/destroy/atomic-disable/knote callback plus C++ setup/weak retirement; deterministic mqueue-lock detach model, adapted XNU task/filter/socket/notification state; actual waitq internals and runtime guest validation are separate gates')
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
