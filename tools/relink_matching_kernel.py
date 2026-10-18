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
FORK = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/process/fork.c.o'
UNLINKAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/unlinkat.c.o'
LINKAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/linkat.c.o'
RENAMEAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/renameat.c.o'
MKDIRAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/stat/mkdirat.c.o'
FACCESSAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/faccessat.c.o'
FCHMODAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/fchmodat.c.o'
FCHMOD = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/fchmod.c.o'
FTRUNCATE = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/ftruncate.c.o'
FSYNC = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/fsync.c.o'
FDATASYNC = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/fdatasync.c.o'
LSEEK = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/lseek.c.o'
FLOCK = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/flock.c.o'
RMDIR = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/stat/rmdir.c.o'
TRUNCATE = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/truncate.c.o'
STAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/stat/stat.c.o'
FSTAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/stat/fstat.c.o'
UMASK = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/umask.c.o'
GETDTABLESIZE = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/getdtablesize.c.o'
PROCESS_IDS = tuple('emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/'+name+'.c.o' for name in ('getpid','getppid','getpgrp','getpgid','getsid','setpgid','setsid'))
READLINKAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/readlinkat.c.o'
SYMLINKAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/unistd/symlinkat.c.o'
FSTATAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/stat/fstatat.c.o'
LSTAT = 'emulation/CMakeFiles/emulation.dir/src/xnu_syscall/bsd/impl/stat/lstat.c.o'
GUARDED = 'emulation/CMakeFiles/emulation.dir/src/common/guarded/table.c.o'


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
    if args.fork_object:
        if not args.guarded_object or inputs.count(FORK) != 1 or not args.fork_object.is_file():
            raise ValueError('fork bridge requires a guarded object and unique original fork input')
        replacements[FORK] = args.fork_object.resolve(strict=True)
    if args.lstat_object and not args.lstat_bridge:
        raise ValueError('lstat object requires its TLS bridge')
    if args.lstat_bridge and not (args.lstat_object or args.paths_object):
        raise ValueError('TLS bridge requires lstat or grouped paths object')
    if args.lstat_bridge and not args.lstat_bridge.is_file():
        raise ValueError('missing lstat TLS bridge')
    if args.lstat_object:
        if inputs.count(LSTAT) != 1 or not args.lstat_object.is_file():
            raise ValueError('missing unique lstat input')
        replacements[LSTAT] = args.lstat_object.resolve(strict=True)
    if args.unlinkat_object:
        if inputs.count(UNLINKAT) != 1 or not args.unlinkat_object.is_file():
            raise ValueError('missing unique unlinkat input')
        replacements[UNLINKAT] = args.unlinkat_object.resolve(strict=True)
    if args.linkat_object:
        if inputs.count(LINKAT) != 1 or not args.linkat_object.is_file():
            raise ValueError('missing unique linkat input')
        replacements[LINKAT] = args.linkat_object.resolve(strict=True)
    if args.renameat_object:
        if inputs.count(RENAMEAT) != 1 or not args.renameat_object.is_file():
            raise ValueError('missing unique renameat input')
        replacements[RENAMEAT] = args.renameat_object.resolve(strict=True)
    if args.mkdirat_object:
        if inputs.count(MKDIRAT) != 1 or not args.mkdirat_object.is_file():
            raise ValueError('missing unique mkdirat input')
        replacements[MKDIRAT] = args.mkdirat_object.resolve(strict=True)
    if args.faccessat_object:
        if inputs.count(FACCESSAT) != 1 or not args.faccessat_object.is_file():
            raise ValueError('missing unique faccessat input')
        replacements[FACCESSAT] = args.faccessat_object.resolve(strict=True)
    if args.fchmodat_object:
        if inputs.count(FCHMODAT) != 1 or not args.fchmodat_object.is_file():
            raise ValueError('missing unique fchmodat input')
        replacements[FCHMODAT] = args.fchmodat_object.resolve(strict=True)
    if args.paths_object:
        if any([args.symlinkat_object, args.readlinkat_object, args.unlinkat_object, args.linkat_object, args.renameat_object, args.mkdirat_object, args.faccessat_object, args.fchmodat_object, args.lstat_object, args.fstatat_object]):
            raise ValueError('grouped paths object conflicts with individual pathname objects')
        if not args.paths_object.is_file() or not args.lstat_bridge:
            raise ValueError('grouped paths object requires its object and original thread-cwd bridge')
        for name in (SYMLINKAT, READLINKAT, UNLINKAT, LINKAT, RENAMEAT, MKDIRAT, FACCESSAT, FCHMODAT, RMDIR, TRUNCATE, LSTAT, FSTATAT, STAT, FSTAT):
            if inputs.count(name) != 1:
                raise ValueError('missing unique grouped pathname input: ' + name)
            replacements[name] = args.paths_object.resolve(strict=True)
    if args.process_ids_object:
        if not args.process_ids_object.is_file():
            raise ValueError('missing process identifier object')
        for name in PROCESS_IDS:
            if inputs.count(name) != 1:
                raise ValueError('missing unique process identifier input: ' + name)
            replacements[name] = args.process_ids_object.resolve(strict=True)
    if args.fd_object:
        if any([args.fchmod_object, args.ftruncate_object, args.fsync_object, args.fdatasync_object]):
            raise ValueError('grouped fd object conflicts with individual descriptor objects')
        if not args.fd_object.is_file():
            raise ValueError('missing grouped descriptor object')
        for name in (FCHMOD, FTRUNCATE, FSYNC, FDATASYNC, LSEEK, FLOCK, UMASK, GETDTABLESIZE):
            if inputs.count(name) != 1:
                raise ValueError('missing unique grouped descriptor input: ' + name)
            replacements[name] = args.fd_object.resolve(strict=True)
    if args.fchmod_object:
        if inputs.count(FCHMOD) != 1 or not args.fchmod_object.is_file():
            raise ValueError('missing unique fchmod input')
        replacements[FCHMOD] = args.fchmod_object.resolve(strict=True)
    if args.ftruncate_object:
        if inputs.count(FTRUNCATE) != 1 or not args.ftruncate_object.is_file():
            raise ValueError('missing unique ftruncate input')
        replacements[FTRUNCATE] = args.ftruncate_object.resolve(strict=True)
    if args.fsync_object:
        if inputs.count(FSYNC) != 1 or not args.fsync_object.is_file():
            raise ValueError('missing unique fsync input')
        replacements[FSYNC] = args.fsync_object.resolve(strict=True)
    if args.fdatasync_object:
        if inputs.count(FDATASYNC) != 1 or not args.fdatasync_object.is_file():
            raise ValueError('missing unique fdatasync input')
        replacements[FDATASYNC] = args.fdatasync_object.resolve(strict=True)
    if args.readlinkat_object:
        if inputs.count(READLINKAT) != 1 or not args.readlinkat_object.is_file():
            raise ValueError('missing unique readlinkat input')
        replacements[READLINKAT] = args.readlinkat_object.resolve(strict=True)
    if args.symlinkat_object:
        if inputs.count(SYMLINKAT) != 1 or not args.symlinkat_object.is_file():
            raise ValueError('missing unique symlinkat input')
        replacements[SYMLINKAT] = args.symlinkat_object.resolve(strict=True)
    if args.fstatat_object:
        if inputs.count(FSTATAT) != 1 or not args.fstatat_object.is_file():
            raise ValueError('missing unique fstatat input')
        replacements[FSTATAT] = args.fstatat_object.resolve(strict=True)
    if args.guarded_object:
        if inputs.count(GUARDED) != 1 or not args.guarded_object.is_file():
            raise ValueError('missing unique guarded-table input')
        replacements[GUARDED] = args.guarded_object.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=False)
    if args.process_object:
        replacements[PROC] = compile_legacy(build, source, output)
    record = {'scope': 'Matching local x86_64 runtime; original objects may contain local modifications',
              'source': str(source), 'build': str(build), 'original_sha256': digest(original),
              'original_link_sha256': digest(link), 'variants': {},
              'inputs': {str((kernel/x).resolve()): digest((kernel/x).resolve()) for x in inputs}}
    for variant in ('original', 'rust'):
        selected = []
        used_replacements = set()
        for name in inputs:
            if variant == 'rust' and name in replacements:
                path = str(replacements[name])
                if path in used_replacements:
                    continue
                used_replacements.add(path)
            else:
                path = str((kernel/name).resolve(strict=True))
            selected.append(path)
        if variant == 'rust' and args.process_object:
            selected.append(str(args.process_object.resolve(strict=True)))
        if variant == 'rust' and args.lstat_bridge:
            selected.append(str(args.lstat_bridge.resolve(strict=True)))
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
                      '_libsimple_lock_lock': 'liblibsimple_darling.a(dearust_simple.o)'}
            if args.process_object:
                wanted['_sys_proc_info'] = args.process_object.name
                wanted['_darling_legacy_proc_info'] = 'legacy-proc-info.o'
                nm = run(['llvm-nm', '--format=darwin', target])
                if not any('private external' in line and line.rstrip().endswith('_darling_legacy_proc_info') for line in nm.splitlines()):
                    raise ValueError('legacy fallback must remain private external')
            if args.lstat_object:
                wanted['_dearust_get_perthread_wd'] = args.lstat_bridge.name
                wanted['_sys_lstat'] = args.lstat_object.name
                wanted['_sys_lstat64'] = args.lstat_object.name
            if args.unlinkat_object:
                wanted['_sys_unlinkat'] = args.unlinkat_object.name
            if args.linkat_object:
                wanted['_sys_linkat'] = args.linkat_object.name
            if args.renameat_object:
                wanted['_sys_renameat'] = args.renameat_object.name
            if args.mkdirat_object:
                wanted['_sys_mkdirat'] = args.mkdirat_object.name
            if args.faccessat_object:
                wanted['_sys_faccessat'] = args.faccessat_object.name
            if args.fchmodat_object:
                wanted['_sys_fchmodat'] = args.fchmodat_object.name
                wanted['_do_linux_fchmodat'] = args.fchmodat_object.name
            if args.paths_object:
                wanted['_sys_symlinkat'] = args.paths_object.name
                wanted['_sys_readlinkat'] = args.paths_object.name
                wanted['_sys_unlinkat'] = args.paths_object.name
                wanted['_sys_linkat'] = args.paths_object.name
                wanted['_sys_renameat'] = args.paths_object.name
                wanted['_sys_mkdirat'] = args.paths_object.name
                wanted['_sys_faccessat'] = args.paths_object.name
                wanted['_sys_fchmodat'] = args.paths_object.name
                wanted['_sys_rmdir'] = args.paths_object.name
                wanted['_dearust_get_perthread_wd'] = args.lstat_bridge.name
                wanted['_sys_lstat'] = args.paths_object.name
                wanted['_sys_lstat64'] = args.paths_object.name
                wanted['_sys_fstatat'] = args.paths_object.name
                wanted['_sys_fstatat64'] = args.paths_object.name
                wanted['_sys_stat'] = args.paths_object.name
                wanted['_sys_stat64'] = args.paths_object.name
                wanted['_sys_fstat'] = args.paths_object.name
                wanted['_sys_fstat64'] = args.paths_object.name
                wanted['_sys_truncate'] = args.paths_object.name
                wanted['_do_linux_fchmodat'] = args.paths_object.name
            if args.process_ids_object:
                wanted['_sys_getpid'] = args.process_ids_object.name
                wanted['_sys_getppid'] = args.process_ids_object.name
                wanted['_sys_getpgrp'] = args.process_ids_object.name
                wanted['_sys_getpgid'] = args.process_ids_object.name
                wanted['_sys_getsid'] = args.process_ids_object.name
                wanted['_sys_setpgid'] = args.process_ids_object.name
                wanted['_sys_setsid'] = args.process_ids_object.name
            if args.fd_object:
                wanted['_sys_fchmod'] = args.fd_object.name
                wanted['_sys_ftruncate'] = args.fd_object.name
                wanted['_sys_fsync'] = args.fd_object.name
                wanted['_sys_fdatasync'] = args.fd_object.name
                wanted['_sys_lseek'] = args.fd_object.name
                wanted['_sys_flock'] = args.fd_object.name
                wanted['_sys_fsync_nocancel'] = args.fd_object.name
                wanted['_sys_umask'] = args.fd_object.name
                wanted['_sys_getdtablesize'] = args.fd_object.name
            if args.fchmod_object:
                wanted['_sys_fchmod'] = args.fchmod_object.name
            if args.ftruncate_object:
                wanted['_sys_ftruncate'] = args.ftruncate_object.name
            if args.fsync_object:
                wanted['_sys_fsync'] = args.fsync_object.name
                wanted['_sys_fsync_nocancel'] = args.fsync_object.name
            if args.fdatasync_object:
                wanted['_sys_fdatasync'] = args.fdatasync_object.name
            if args.readlinkat_object:
                wanted['_sys_readlinkat'] = args.readlinkat_object.name
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
            details['provenance'] = link_provenance(mapfile.read_text(), wanted)
        record['variants'][variant] = details
    if args.lstat_bridge:
        record['lstat_bridge'] = {'path': str(args.lstat_bridge.resolve()), 'sha256': digest(args.lstat_bridge)}
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
    parser.add_argument('--guarded-object', type=Path)
    parser.add_argument('--fork-object', type=Path)
    parser.add_argument('--unlinkat-object', type=Path)
    parser.add_argument('--linkat-object', type=Path)
    parser.add_argument('--renameat-object', type=Path)
    parser.add_argument('--mkdirat-object', type=Path)
    parser.add_argument('--faccessat-object', type=Path)
    parser.add_argument('--fchmodat-object', type=Path)
    parser.add_argument('--paths-object', type=Path)
    parser.add_argument('--process-ids-object', type=Path)
    parser.add_argument('--fd-object', type=Path)
    parser.add_argument('--fchmod-object', type=Path)
    parser.add_argument('--ftruncate-object', type=Path)
    parser.add_argument('--fsync-object', type=Path)
    parser.add_argument('--fdatasync-object', type=Path)
    parser.add_argument('--readlinkat-object', type=Path)
    parser.add_argument('--symlinkat-object', type=Path)
    parser.add_argument('--fstatat-object', type=Path)
    parser.add_argument('--lstat-object', type=Path)
    parser.add_argument('--lstat-bridge', type=Path)
    args = parser.parse_args()
    print(json.dumps(integrate(args)['variants'], indent=2))


if __name__ == '__main__':
    main()
