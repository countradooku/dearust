#!/usr/bin/env python3
"""Map observed upstream CMake targets to verified Cargo artifact slices.

Graph capture is not a Cargo replacement. Legacy targets remain unretired;
optional configurations and custom command bodies need separate expansion.
"""
import argparse
import json
from pathlib import Path

REPLACEMENTS = {
    'elfdep': ('dearust', 'elfdep', 'host-artifact-verified'),
    'getuuid': ('dearust', 'getuuid', 'host-artifact-verified'),
    'bsdln': ('dearust', 'bsdln', 'host-artifact-verified'),
    'darling-coredump': ('dearust-coredump', 'darling-coredump', 'host-artifact-verified'),
    'wrapgen': ('dearust', 'wrapgen', 'partial-loader-policy'),
    'libsimple_darling': ('dearust-libsimple', 'liblibsimple_darling.a', 'partial-darling-x86_64-consumer-linked'),
    'libsimple_darlingserver': ('dearust-libsimple', 'dearust_simple', 'partial-native-only'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', type=Path, default=Path('upstream-cmake-graph.json'))
    parser.add_argument('--output', type=Path, default=Path('migration-targets.json'))
    args = parser.parse_args()
    graph = json.loads(args.graph.read_text())
    entries = []
    found = set()
    for reference, target in graph['objects'].items():
        if 'type' not in target:
            continue
        name = target['name']
        entry = {'name': name, 'id': target['id'], 'type': target['type'],
                 'graph_reference': reference, 'status': 'pending',
                 'legacy_target_retired': False}
        if name in REPLACEMENTS:
            package, artifact, status = REPLACEMENTS[name]
            entry.update(cargo_package=package, cargo_artifact=artifact, status=status)
            found.add(name)
        if name in {'emulation', 'emulation_dyld'}:
            entry['verified_source_slices'] = [{
                'source': 'src/external/xnu/darling/src/libsystem_kernel/emulation/src/' + source,
                'cargo_package': package, 'cargo_artifact': artifact,
                'scope': ('Native real-consumer execution and x86_64 Mach-O actual system_kernel link verified; i386 replacement/startup pending'
                          if name == 'emulation' else 'Same Rust x86_64 source slices built; actual emulation_dyld/static consumer link remains pending'),
            } for source, package, artifact in [('conversion/errno.c','dearust-errno','errno.o'), ('linux_premigration/ext/futex.c','dearust-futex','futex.o')]]
        if name == 'emulation':
            entry.setdefault('verified_source_slices', []).append({
                'source': 'src/external/xnu/darling/src/libsystem_kernel/emulation/src/xnu_syscall/bsd/impl/misc/proc_info.c',
                'cargo_package': 'dearust-process', 'cargo_artifact': 'process.o',
                'scope': 'Partial Rust process dispatcher; original fallback flavors retained. Matching local runtime integration recorded in lockset-process-verification.json',
            })
            for source, package, artifact in (
                    ('common/guarded/table.c', 'dearust-guarded', 'guarded.o'),
                    ('xnu_syscall/bsd/impl/stat/lstat.c', 'dearust-lstat', 'lstat.o'),
                    ('xnu_syscall/bsd/impl/stat/fstatat.c', 'dearust-fstatat', 'fstatat.o'),
                    ('xnu_syscall/bsd/impl/unistd/symlinkat.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/unistd/readlinkat.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/unistd/unlinkat.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/unistd/linkat.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/unistd/renameat.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/stat/mkdirat.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/unistd/faccessat.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/unistd/fchmodat.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/unistd/fchmod.c', 'dearust-fd', 'fd.o'),
                    ('xnu_syscall/bsd/impl/unistd/ftruncate.c', 'dearust-fd', 'fd.o'),
                    ('xnu_syscall/bsd/impl/unistd/fsync.c', 'dearust-fd', 'fd.o'),
                    ('xnu_syscall/bsd/impl/unistd/fdatasync.c', 'dearust-fd', 'fd.o'),
                    ('xnu_syscall/bsd/impl/unistd/lseek.c', 'dearust-fd', 'fd.o'),
                    ('xnu_syscall/bsd/impl/unistd/flock.c', 'dearust-fd', 'fd.o'),
                    ('xnu_syscall/bsd/impl/unistd/umask.c', 'dearust-fd', 'fd.o'),
                    ('xnu_syscall/bsd/impl/unistd/getdtablesize.c', 'dearust-fd', 'fd.o'),
                    ('xnu_syscall/bsd/impl/stat/rmdir.c', 'dearust-path', 'paths.o'),
                    ('xnu_syscall/bsd/impl/unistd/truncate.c', 'dearust-path', 'paths.o')):
                entry['verified_source_slices'].append({
                    'source': 'src/external/xnu/darling/src/libsystem_kernel/emulation/src/' + source,
                    'cargo_package': package, 'cargo_artifact': artifact,
                    'scope': 'x86_64 Rust slice with existing OS/ABI boundaries; matching runtime checks in runtime-paths-verification.json, runtime-control-verification.json, runtime-descriptors-verification.json, runtime-fdops-verification.json, runtime-permissions-verification.json, runtime-fileops-verification.json, runtime-performance-verification.json, runtime-next-verification.json and runtime-expansion-verification.json. Other architectures and original target retirement remain pending.',
                })
        if name == 'emulation':
            for source in ('getpid', 'getppid', 'getpgrp', 'getpgid', 'getsid', 'setpgid', 'setsid'):
                entry['verified_source_slices'].append({
                    'source': 'src/external/xnu/darling/src/libsystem_kernel/emulation/src/xnu_syscall/bsd/impl/unistd/' + source + '.c',
                    'cargo_package': 'dearust-process', 'cargo_artifact': 'process-ids.o',
                    'scope': 'x86_64 identifier-only Rust artifact; matching runtime checks in runtime-control-verification.json and runtime-identifiers-verification.json. Query-only process.o integration remains matching-only. Other architectures and original target retirement remain pending.',
                })
        if name == 'darlingserver':
            entry['verified_source_slices'] = [{
                'source': 'src/external/darlingserver/duct-tape/xnu/osfmk/kern/sync_lock.c',
                'cargo_package': 'dearust-lockset', 'cargo_artifact': 'libdearust_lockset.a',
                'scope': 'Rust lockset ownership/state; original server relink with XNU bridge. Original target remains pending.',
            }]
        if name == 'system_kernel':
            entry['legacy_consumer_integration'] = {
                'record': 'system-kernel-integration.json',
                'scope': 'Full original x86_64 final link uses Cargo Rust libsimple/futex/errno slices; other inputs remain original, i386 final slice preserved, actual launch pending',
                'cargo_target_replacement': False,
            }
        entries.append(entry)
    if found != REPLACEMENTS.keys():
        raise ValueError(f'expected native targets missing: {sorted(REPLACEMENTS.keys()-found)}')
    entries.sort(key=lambda entry: (entry['name'], entry['id']))
    output = {
        'schema_version': 1,
        'upstream_commit': '60ba801decee7a00782f74f6be4c8ffb013f79ff',
        'scope': 'Every emitted target in the captured default configuration. Source/install/custom commands, optional configurations and runtime ABI require additional gates; no legacy target retirement is claimed.',
        'counts': {'observed_default_targets': len(entries), 'with_cargo_artifact_slice': len(found),
                   'pending_targets': len(entries)-len(found), 'legacy_targets_retired': 0},
        'additional_optional_artifacts': [{'name': 'print_wrapped_elf', 'cargo_package': 'dearust', 'status': 'host-artifact-verified', 'absent_from_default_configuration': True}],
        'targets': entries,
    }
    args.output.write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps(output['counts'], sort_keys=True))


if __name__ == '__main__':
    main()
