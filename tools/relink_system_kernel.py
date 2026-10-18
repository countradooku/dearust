#!/usr/bin/env python3
"""Integrate Rust x86-64 slices into the real pinned libsystem_kernel final link.

Uses an already-built original CMake tree as a legacy integration reference.
Does not run CMake, modify that tree, add stubs, or allow unresolved symbols.
The original final i386 dylib slice is preserved byte-for-byte, not ported.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from check_errno import DARLING_PIN, XNU_PIN

FORK = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/process/fork.c.o'
SYMLINKAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/symlinkat.c.o'
FSTATAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/stat/fstatat.c.o'
LSTAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/stat/lstat.c.o'
KERNEL = Path('src/external/xnu/darling/src/libsystem_kernel')
REPLACEMENTS = {
    'emulation/CMakeFiles/emulation.dir/src/conversion/errno.c.o': 'objects/errno.o',
    'emulation/CMakeFiles/emulation.dir/src/linux_premigration/ext/futex.c.o': 'objects/futex.o',
}


def run(args, cwd=None):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, capture_output=True,
                            text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'command failed ({result.returncode}): {args}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def symbols(path, mode):
    output = run(['llvm-nm', '--arch=x86_64', '--extern-only', mode, '--format=posix', path])
    return {line.split()[0] for line in output.splitlines() if line.strip()}


def link_provenance(text, wanted):
    files = {}
    found = {}
    for line in text.splitlines():
        if line.startswith('[') and ']' in line:
            number, path = line.split(']', 1)
            files[int(number[1:].strip())] = path.strip()
        elif line.startswith('0x') and '[' in line:
            fields = line.split()
            name = fields[-1]
            if name in wanted:
                index = int(line.split('[', 1)[1].split(']', 1)[0].strip())
                found[name] = files[index]
    if found.keys() != wanted.keys():
        raise ValueError(f'expected runtime consumer symbols missing from link map: {wanted.keys()-found.keys()}')
    for name, fragment in wanted.items():
        if fragment not in found[name]:
            raise ValueError(f'{name} came from {found[name]}, expected {fragment}')
    return found


def integrate(args):
    build = args.build.resolve(strict=True)
    runtime = args.runtime.resolve(strict=True)
    output = args.output.resolve()
    if output == build or build in output.parents:
        raise ValueError('integration output must be outside the original build tree')
    source = next((line.partition('=')[2] for line in (build/'CMakeCache.txt').read_text().splitlines()
                   if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL=')), None)
    if source is None:
        raise ValueError('original CMake source directory missing')
    if run(['git', '-C', source, 'rev-parse', 'HEAD']).strip() != DARLING_PIN:
        raise ValueError('original source must have the exact pinned superproject HEAD')
    if run(['git', '-C', str(Path(source)/'src/external/xnu'), 'rev-parse', 'HEAD']).strip() != XNU_PIN:
        raise ValueError('original source must have the exact pinned xnu HEAD')
    manifest = (runtime/'darling-runtime-artifacts.tsv').read_text()
    if not manifest.startswith('# target x86_64-apple-darwin\n'):
        raise ValueError('requires staged x86_64-apple-darwin runtime artifacts')
    kernel = build/KERNEL
    original = kernel/'libsystem_kernel.dylib'
    link_file = kernel/'CMakeFiles/system_kernel.dir/link.txt'
    command = shlex.split(link_file.read_text())
    responses = [word for word in command if word.startswith('@')]
    if len(responses) != 1:
        raise ValueError('expected one original final-link response file')
    response = (kernel/responses[0][1:]).resolve(strict=True)
    inputs = shlex.split(response.read_text())
    replacements = {}
    for name, replacement in REPLACEMENTS.items():
        if inputs.count(name) != 1:
            raise ValueError('expected exactly one original object: ' + name)
        path = runtime/replacement
        if not path.is_file():
            raise ValueError('missing Rust runtime object: ' + replacement)
        replacements[name] = path
    if args.fork_object:
        if not args.guarded_object or inputs.count(FORK) != 1 or not args.fork_object.is_file():
            raise ValueError('fork bridge requires a guarded object and unique original fork input')
        replacements[FORK] = args.fork_object.resolve(strict=True)
    if bool(args.lstat_object) != bool(args.lstat_bridge):
        raise ValueError('lstat object and TLS bridge must be supplied together')
    if args.lstat_bridge and not args.lstat_bridge.is_file():
        raise ValueError('missing lstat TLS bridge')
    if args.lstat_object:
        if inputs.count(LSTAT) != 1 or not args.lstat_object.is_file():
            raise ValueError('missing unique lstat input')
        replacements[LSTAT] = args.lstat_object.resolve(strict=True)
    if args.symlinkat_object:
        if inputs.count(SYMLINKAT) != 1 or not args.symlinkat_object.is_file():
            raise ValueError('missing unique symlinkat input')
        replacements[SYMLINKAT] = args.symlinkat_object.resolve(strict=True)
    if args.fstatat_object:
        if inputs.count(FSTATAT) != 1 or not args.fstatat_object.is_file():
            raise ValueError('missing unique fstatat input')
        replacements[FSTATAT] = args.fstatat_object.resolve(strict=True)
    if args.guarded_object:
        name = 'emulation/CMakeFiles/emulation.dir/src/common/guarded/table.c.o'
        if inputs.count(name) != 1 or not args.guarded_object.is_file():
            raise ValueError('missing unique guarded-table input')
        replacements[name] = args.guarded_object.resolve(strict=True)
    archive = (build/'src/libsimple-darling/liblibsimple_darling.a').resolve(strict=True)
    # Compile/link commands are executed directly as argv, never as shell text.
    # Every original production input remains, except the declared source slices.
    new_inputs = [str(replacements.get(name, (kernel/name).resolve(strict=True))) for name in inputs]
    if args.lstat_bridge:
        new_inputs.append(str(args.lstat_bridge.resolve(strict=True)))
    for name in new_inputs:
        if not Path(name).is_file():
            raise ValueError('missing original runtime link object: ' + name)
    # A fresh exclusively owned directory prevents accidental output replacement.
    output.mkdir(parents=True, exist_ok=False)
    new_response = output/'objects.rsp'
    new_response.write_text(shlex.join(new_inputs) + '\n')
    linked64 = output/'libsystem_kernel-rust-x86_64.dylib'
    linkmap = output/'system-kernel-x86_64.map'
    argv = []
    index = 0
    replaced_archive = 0
    while index < len(command):
        word = command[index]
        if word == '-arch':
            if index + 1 >= len(command):
                raise ValueError('malformed original architecture argument')
            index += 2
            continue
        if word == '-o':
            argv += ['-o', str(linked64)]
            index += 2
            continue
        if word == responses[0]:
            argv.append('@' + str(new_response))
        elif not word.startswith('-') and (kernel/word).resolve() == archive:
            argv.append(str(runtime/'lib/liblibsimple_darling.a'))
            replaced_archive += 1
        else:
            argv.append(word)
        index += 1
    if replaced_archive != 1 or not linked64.name in ' '.join(argv):
        raise ValueError('original final link archive/output shape mismatch')
    argv += ['-arch', 'x86_64', '-Wl,-map,' + str(linkmap)]
    run(argv, cwd=kernel)
    original_exports = symbols(original, '--defined-only')
    migrated_exports = symbols(linked64, '--defined-only')
    if migrated_exports != original_exports:
        raise ValueError(f'public runtime symbols changed; added {sorted(migrated_exports-original_exports)}, missing {sorted(original_exports-migrated_exports)}')
    original_imports = symbols(original, '--undefined-only')
    migrated_imports = symbols(linked64, '--undefined-only')
    if migrated_imports != original_imports:
        raise ValueError(f'runtime imports changed; added {sorted(migrated_imports-original_imports)}, removed {sorted(original_imports-migrated_imports)}')
    wanted = {
        '_errno_linux_to_bsd': 'objects/errno.o',
        '___linux_futex_reterr': 'objects/futex.o',
        '___linux_futex': 'objects/futex.o',
        '_libsimple_lock_lock': 'liblibsimple_darling.a(dearust_simple.o)',
        '_guard_table_add': 'emulation.dir/src/common/guarded/table.c.o',
        '_close_internal': 'emulation.dir/src/xnu_syscall/bsd/impl/unistd/close.c.o',
        '_linux_syscall': 'emulation.dir/src/linux_premigration/linux-syscall.S.o',
        '_cerror': 'libsyscall.dir/custom/errno.c.o',
        '___simple_abort': 'emulation.dir/src/common/simple.c.o',
    }
    if args.lstat_object:
        wanted['_dearust_get_perthread_wd'] = args.lstat_bridge.name
        wanted['_sys_lstat'] = args.lstat_object.name
        wanted['_sys_lstat64'] = args.lstat_object.name
    if args.symlinkat_object:
        wanted['_sys_symlinkat'] = args.symlinkat_object.name
    if args.fstatat_object:
        wanted['_sys_fstatat'] = args.fstatat_object.name
        wanted['_sys_fstatat64'] = args.fstatat_object.name
    if args.fork_object:
        wanted['_sys_fork'] = args.fork_object.name
        for name in ('prefork', 'postfork_parent'):
            wanted['_guard_table_' + name] = args.guarded_object.name
    if args.guarded_object:
        for name in ('add', 'modify', 'remove', 'check', 'postfork_child'):
            wanted['_guard_table_' + name] = args.guarded_object.name
    provenance = link_provenance(linkmap.read_text(), wanted)
    original32 = output/'original-i386.dylib'
    run(['llvm-lipo', original, '-thin', 'i386', '-output', original32])
    universal = output/'libsystem_kernel.dylib'
    run(['llvm-lipo', '-create', original32, linked64, '-output', universal])
    check32 = output/'preserved-i386-check.dylib'
    run(['llvm-lipo', universal, '-thin', 'i386', '-output', check32])
    if check32.read_bytes() != original32.read_bytes():
        raise ValueError('unported i386 slice changed')
    def normalize(value):
        return value.replace(str(build), '$BUILD').replace(str(runtime), '$RUNTIME').replace(str(output), '$OUTPUT')
    return {
        'scope': 'Actual full upstream system_kernel final link with declared Rust x86_64 source slices; original i386 final slice preserved, all other implementation remains upstream',
        'darling_pin': DARLING_PIN, 'xnu_pin': XNU_PIN,
        'original_response_objects': len(inputs),
        'replaced_objects': {name: digest(path) for name, path in replacements.items()},
        'rust_archive_sha256': digest(runtime/'lib/liblibsimple_darling.a'),
        'original_link_command_sha256': hashlib.sha256(normalize(link_file.read_text()).encode()).hexdigest(),
        'original_response_sha256': hashlib.sha256(normalize(response.read_text()).encode()).hexdigest(),
        'linked_x86_64_sha256': digest(linked64), 'universal_dylib_sha256': digest(universal),
        'original_dylib_sha256': digest(original),
        'public_defined_symbol_count': len(original_exports), 'public_symbol_sets_identical': True,
        'undefined_symbol_count': len(original_imports), 'import_symbol_sets_identical': True,
        'original_non_architecture_link_flags_preserved': True, 'stubs_or_dynamic_lookup_added': False,
        'actual_symbol_provenance': {name: normalize(path) for name, path in provenance.items()},
        'i386_preserved_byte_identical': True, 'i386_slice_sha256': digest(original32),
        'runtime_execution': 'Real guarded-table/close consumers execute through this source chain in native ELF differential tests; this Mach-O dylib has not been launched under Darling',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--guarded-object', type=Path)
    parser.add_argument('--fork-object', type=Path)
    parser.add_argument('--symlinkat-object', type=Path)
    parser.add_argument('--fstatat-object', type=Path)
    parser.add_argument('--lstat-object', type=Path)
    parser.add_argument('--lstat-bridge', type=Path)
    args = parser.parse_args()
    record = integrate(args)
    if args.report:
        args.report.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({key: record[key] for key in ['original_response_objects', 'public_defined_symbol_count', 'public_symbol_sets_identical', 'undefined_symbol_count', 'import_symbol_sets_identical', 'i386_preserved_byte_identical']}))


if __name__ == '__main__':
    main()
