#!/usr/bin/env python3
"""Capture exact committed dtape acquisition sources and selected bridge provenance."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

PIN = '89751e64bc6c2082f7725061824ee0e33395b0de'
PATHS = ['src/task.c', 'src/thread.c', 'src/semaphore.c', 'src/memory.c',
         'src/locks.c', 'src/condvar.c', 'src/psynch.c', 'pthread/kern_synch.c',
         'xnu/osfmk/kern/ipc_tt.c', 'xnu/osfmk/kern/ipc_kobject.c',
         'xnu/osfmk/kern/turnstile.c', 'xnu/osfmk/kern/waitq.c',
         'xnu/osfmk/kern/sync_sema.c', 'xnu/osfmk/ipc/ipc_importance.c',
         'xnu/osfmk/ipc/ipc_space.c']
MATRIX = [
    dict(path='src/task.c', boundary='task wrapper malloc', status='existing checked NULL',
         ownership='No C subobjects, references, IPC or context disposal ownership acquired.', test='actual malloc denial 1'),
    dict(path='src/task.c', boundary='dtape_vm_map_create malloc', status='implemented owned fix',
         ownership='Check NULL before IPC initialization. Destroy unheld primitive task lock and free only task wrapper. Mutex/condvar initialization owns no dynamic storage; no C context-dispose callback is owed for an unreturned handle.', test='actual malloc denial 2; tracked wrapper allocation is freed'),
    dict(path='xnu/osfmk/kern/ipc_tt.c', boundary='ipc_task_init / ipc_space_create', status='unfinished',
         ownership='ipc_space_create itself frees a failed table-allocation wrapper and returns KERN_RESOURCE_SHORTAGE; ipc_task_init panics on that error. Requires a checked dtape-only IPC initializer and an explicit initialized-space/port ownership plan.'),
    dict(path='xnu/osfmk/kern/ipc_kobject.c', boundary='task/thread control and labeled IPC ports', status='unfinished',
         ownership='ipc_kobject_alloc_port panics on failed ipc_port_alloc_kernel. Labeled path can already own a port when label allocation panics. Do not replace global panic contracts or call full task/thread destruction on guessed partial IPC state.'),
    dict(path='src/task.c', boundary='task_importance_init_from_parent', status='unfinished',
         ownership='Importance inheritance asserts non-NULL acquired structures; ownership can add a task reference. Requires checked inheritance and cleanup on the dedicated kernel lane after complete partial-state provenance.'),
    dict(path='xnu/osfmk/ipc/ipc_importance.c', boundary='ipc_importance_for_task', status='unfinished',
         ownership='Can return IIT_NULL after failed zone allocation; dtape task creation unconditionally calls ipc_importance_task_release. Existing initialized IPC, VM map and locks must remain owned on failure; normal destructor assumes later psynch initialization and is not an admissible guessed rollback.'),
    dict(path='pthread/kern_synch.c', boundary='_pth_proc_hashinit / hashinit', status='unfinished',
         ownership='hashinit returns NULL from kheap_alloc; _pth_proc_hashinit panics. Occurs after task IPC enable and importance ownership. Need checked private hash acquisition before activation or explicit partial initialization state.'),
    dict(path='src/thread.c', boundary='thread wrapper malloc', status='existing checked NULL',
         ownership='No task reference/list attachment, IPC port or context-dispose ownership acquired.', test='actual malloc denial 1'),
    dict(path='xnu/osfmk/kern/turnstile.c', boundary='turnstile zone allocation', status='implemented owned fix',
         ownership='New dtape-only checked allocation entry retains exact turnstile_init and debug-list behavior after allocation. Acquire before IPC/task reference/list attachment; failed allocation frees unexposed thread wrapper. Generic XNU turnstile_alloc contract unchanged. waitq_init for these fixed flags has no allocation.', test='actual thread malloc denial 2; parent thread/active/semaphore/refcount unchanged'),
    dict(path='src/thread.c', boundary='ipc_thread_init after reserved turnstile', status='unfinished',
         ownership='Unchecked/panic control-port allocation remains. Checked turnstile is now already owned; later checked IPC initialization must return explicit failure and release that turnstile using its proved initialized state.'),
    dict(path='src/semaphore.c', boundary='semaphore wrapper malloc', status='existing checked NULL',
         ownership='No XNU semaphore acquired.', test='actual first malloc denial'),
    dict(path='xnu/osfmk/kern/sync_sema.c', boundary='semaphore zone and waitq initialization', status='existing checked error and validated',
         ownership='semaphore_create returns KERN_RESOURCE_SHORTAGE on NULL zone allocation. dtape wrapper frees itself on failure. Fixed waitq policy initializes primitive fields/queues without allocation; owner-inactive path frees the initialized semaphore before returning KERN_INVALID_TASK.', test='actual second malloc denial; wrapper allocation tracked and freed'),
    dict(path='src/thread.c', boundary='active_thread_count retirement', status='implemented owned fix',
         ownership='Exact pin and selected bridge increment on creation but never decrement in dying/destruction. New helper clears thread.active under thread_lock, decrements task active count once, wakes zero-count waiters, and runs on dying plus final-destroy fallback. Thread-held task reference remains until final destruction.', test='prior-behavior derivative must fail; successful actual lifecycle restores thread/active/semaphore/refcounts; repeated dying is idempotent'),
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    base = json.loads((candidate / 'build.json').read_text())
    repo = Path(base['source']) / 'src/external/darlingserver'
    duct = repo / 'duct-tape'
    sources = {}
    for relative in PATHS:
        pinned = subprocess.run(['git', '-C', str(repo), 'show', PIN + ':duct-tape/' + relative],
                                text=True, capture_output=True, check=True).stdout
        captured = output / 'exact-pin' / relative
        captured.parent.mkdir(parents=True, exist_ok=True)
        captured.write_text(pinned)
        current = duct / relative
        sources[relative] = {'committed_sha256': digest(captured), 'working_source': str(current),
                             'working_sha256': digest(current),
                             'working_matches_pin': current.read_text() == pinned}
        diff = ''.join(difflib.unified_diff(pinned.splitlines(True), current.read_text().splitlines(True),
                                          fromfile=PIN + ':duct-tape/' + relative, tofile=str(current)))
        if diff: captured.with_name(captured.name + '.working.diff').write_text(diff)
    relink = json.loads((candidate / 'linked/relink.json').read_text())
    bridges = []
    for row in relink['inputs']:
        if row['source'] not in ('src/task.c', 'src/thread.c'): continue
        obj = Path(row['selected'])
        adjacent = obj.with_suffix('')
        if digest(obj) != row['selected_sha256']:
            raise ValueError('selected object changed: ' + str(obj))
        build_report = adjacent.parent.parent / 'bridge-build.json'
        command_row = next(item for item in json.loads(build_report.read_text())
                           if item.get('object') == str(obj))
        reproduced = output / ('reproduced-' + adjacent.name + '.o')
        command = list(command_row['command'])
        command[command.index('-o') + 1] = str(reproduced)
        result = subprocess.run(command, cwd=Path(base['build']) / 'src/external/darlingserver/duct-tape',
                                text=True, capture_output=True)
        (output / (adjacent.name + '-rebuild.log')).write_text(result.stdout + result.stderr)
        result.check_returncode()
        bridges.append(dict(source=row['source'], selected_object=str(obj), selected_object_sha256=digest(obj),
                            adjacent_source=str(adjacent), adjacent_source_sha256=digest(adjacent),
                            bridge_build_report=str(build_report), bridge_build_report_sha256=digest(build_report),
                            reproduction_command=command, reproduced_object=str(reproduced),
                            reproduced_object_sha256=digest(reproduced), byte_identical=digest(reproduced) == digest(obj),
                            lockset_callbacks_preserved='darling_lockset_' in adjacent.read_text()))
    report = dict(scope='Exact committed source audit plus matching selected bridge-object reproduction; optional configurations and later IPC failures remain unfinished.',
                  server_pin=PIN, sources=sources, selected_bridges=bridges, boundaries=MATRIX,
                  universal_inner_oom_complete=False)
    (output / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'server_pin': PIN, 'source_files': len(sources),
                      'bridges': bridges, 'boundary_rows': len(MATRIX)}, indent=2))


if __name__ == '__main__':
    main()
