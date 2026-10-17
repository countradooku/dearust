#!/usr/bin/env python3
"""Relink a matching local runtime without changing its source, build or installation.

Unlike the pinned universal-link check, this preserves the locally built RPC ABI
and emits x86_64 only. A process-query slice may retain legacy flavors through a
renamed, hidden copy of the original implementation.
"""
import argparse
import json
from pathlib import Path
import shlex

from relink_system_kernel import KERNEL, REPLACEMENTS, digest, link_provenance, run, symbols

PROC = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/misc/proc_info.c.o'


def compile_legacy(build, source, output):
    directory = build/KERNEL/'emulation'
    flags = {}
    for line in (directory/'CMakeFiles/emulation.dir/flags.make').read_text().splitlines():
        key, sep, value = line.partition(' = ')
        if sep and key in ('C_DEFINES', 'C_INCLUDES', 'C_FLAGS'):
            flags[key] = shlex.split(value)
    if len(flags) != 3:
        raise ValueError('missing original C compile flags')
    original = source/KERNEL/'emulation/src/xnu_syscall/bsd/impl/misc/proc_info.c'
    target = output/'legacy-proc-info.o'
    argv = ['/usr/bin/clang', *flags['C_DEFINES'], *flags['C_INCLUDES'],
            *flags['C_FLAGS'], '-Dsys_proc_info=darling_legacy_proc_info',
            '-c', str(original), '-o', str(target)]
    run(argv, cwd=directory)
    (output/'legacy-compile.json').write_text(json.dumps(argv, indent=2)+'\n')
    return target


def integrate(args):
    build = args.build.resolve(strict=True)
    runtime = args.runtime.resolve(strict=True)
    output = args.output.resolve()
    source = next(Path(line.partition('=')[2]) for line in
                  (build/'CMakeCache.txt').read_text().splitlines()
                  if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='))
    if output == build or build in output.parents or output == source or source in output.parents:
        raise ValueError('output must be outside reference source/build')
    if not (runtime/'darling-runtime-artifacts.tsv').read_text().startswith('# target x86_64-apple-darwin\n'):
        raise ValueError('requires staged Darwin runtime')
    kernel = build/KERNEL
    original = kernel/'libsystem_kernel.dylib'
    link = kernel/'CMakeFiles/system_kernel.dir/link.txt'
    command = shlex.split(link.read_text())
    responses = [x for x in command if x.startswith('@')]
    if len(responses) != 1:
        raise ValueError('expected one link response')
    inputs = shlex.split((kernel/responses[0][1:]).read_text())
    archive = (build/'src/libsimple-darling/liblibsimple_darling.a').resolve(strict=True)
    replacements = {key: runtime/value for key, value in REPLACEMENTS.items()}
    for key, value in replacements.items():
        if inputs.count(key) != 1 or not value.is_file():
            raise ValueError('missing unique runtime replacement '+key)
    if args.process_object and (inputs.count(PROC) != 1 or not args.process_object.is_file()):
        raise ValueError('missing process-query input')
    output.mkdir(parents=True, exist_ok=False)
    if args.process_object:
        replacements[PROC] = compile_legacy(build, source, output)
    record = {'scope': 'Matching local x86_64 runtime; original objects may contain local modifications',
              'source': str(source), 'build': str(build), 'original_sha256': digest(original),
              'original_link_sha256': digest(link), 'variants': {},
              'inputs': {str((kernel/x).resolve()): digest((kernel/x).resolve()) for x in inputs}}
    for variant in ('original', 'rust'):
        selected = [str(replacements[x] if variant == 'rust' and x in replacements
                        else (kernel/x).resolve(strict=True)) for x in inputs]
        if variant == 'rust' and args.process_object:
            selected.append(str(args.process_object.resolve(strict=True)))
        response = output/(variant+'.rsp')
        response.write_text(shlex.join(selected)+'\n')
        target = output/('libsystem_kernel-'+variant+'.dylib')
        mapfile = output/(variant+'.map')
        argv = []
        i = 0
        count = 0
        while i < len(command):
            word = command[i]
            if word == '-arch':
                i += 2
                continue
            if word == '-o':
                argv.extend(['-o', str(target)])
                i += 2
                continue
            if word == responses[0]:
                argv.append('@'+str(response))
            elif variant == 'rust' and not word.startswith('-') and (kernel/word).resolve() == archive:
                argv.append(str(runtime/'lib/liblibsimple_darling.a'))
                count += 1
            else:
                argv.append(word)
            i += 1
        if variant == 'rust' and count != 1:
            raise ValueError('missing unique libsimple replacement')
        argv.extend(['-arch', 'x86_64', '-Wl,-map,'+str(mapfile)])
        (output/(variant+'-argv.json')).write_text(json.dumps(argv, indent=2)+'\n')
        run(argv, cwd=kernel)
        defined = symbols(target, '--defined-only')
        expected = symbols(original, '--defined-only')
        if defined != expected:
            raise ValueError(f'definitions changed: added={defined-expected}, removed={expected-defined}')
        if symbols(target, '--undefined-only') != symbols(original, '--undefined-only'):
            raise ValueError('runtime imports changed')
        details = {'path': str(target), 'sha256': digest(target), 'defined_symbols': len(defined),
                   'imports_match_original': True}
        if variant == 'rust':
            wanted = {'_errno_linux_to_bsd': 'objects/errno.o',
                      '___linux_futex': 'objects/futex.o',
                      '_libsimple_lock_lock': 'liblibsimple_darling.a(darling_simple.o)'}
            if args.process_object:
                wanted['_sys_proc_info'] = args.process_object.name
                wanted['_darling_legacy_proc_info'] = 'legacy-proc-info.o'
                nm = run(['llvm-nm', '--format=darwin', target])
                if not any('private external' in line and line.rstrip().endswith('_darling_legacy_proc_info') for line in nm.splitlines()):
                    raise ValueError('legacy fallback must remain private external')
            details['provenance'] = link_provenance(mapfile.read_text(), wanted)
        record['variants'][variant] = details
    record['replacements'] = {key: {'path': str(value), 'sha256': digest(value)} for key, value in replacements.items()}
    if args.process_object:
        record['process_object_sha256'] = digest(args.process_object)
    (output/'matching-link-report.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'runtime', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--process-object', type=Path)
    args = parser.parse_args()
    print(json.dumps(integrate(args)['variants'], indent=2))


if __name__ == '__main__':
    main()
