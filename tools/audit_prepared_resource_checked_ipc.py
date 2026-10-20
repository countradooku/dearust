#!/usr/bin/env python3
"""Reconcile the preserved inner audit against actual checked-acquisition proofs."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--proof', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    original = json.loads(args.audit.read_text())
    proof = json.loads(args.proof.read_text())
    checked = proof['results']['checked']
    assert checked['task_default_failures'] == [8, 10, 10]
    assert checked['task_inherited_importance_failures'] == [8, 10, 10]
    assert checked['thread_failures'] == [3, 5, 5]
    assert checked['semaphore_all_positions_failures'] == 10
    assert checked['cleanup_allocation_attempts'] == 0
    assert checked['tracked_partial_allocations_remaining'] == 0
    assert sha(Path(proof['server'])) == proof['server_sha256']
    rows = original['boundaries']
    fixes = {
        'ipc_task_init / ipc_space_create': 'Private checked IPC initializer owns the initialized itk lock and acquired space immediately; ipc_space failure returns its error. Stage-aware abort terminates and releases only an acquired space.',
        'task/thread control and labeled IPC ports': 'Private checked port allocator returns every acquired port, including the port preceding a failed label allocation. Unpublished-port abort clears kobjects/alternate ports and destroys each distinct owned port exactly once before any naked send rights or inheritance.',
        'task_importance_init_from_parent': 'Private checked inheritance propagates importance allocation failure. Any acquired base and its task reference remain attached until kernel-lane partial abort explicitly disconnects them.',
        'ipc_importance_for_task': 'Private prepare checks IIT_NULL before release and returns an owned partial task. Partial abort asserts the sole remaining initial task reference after importance disconnect; normal full task destruction is never used for incomplete state.',
        '_pth_proc_hashinit / hashinit': 'Private checked hash initializer returns error without publishing a NULL hash; checked prepare enables task IPC only after successful hash acquisition. Outer p_pthhash is explicitly initialized NULL for partial-state destruction.',
        'ipc_thread_init after reserved turnstile': 'Private checked thread initializer stores each port immediately. A retained parent task reference precedes IPC acquisition. Failure owns only explicit port/turnstile/mutex/task-reference stages; abort never invokes full thread destruction.',
    }
    for row in rows:
        if row['boundary'] in fixes:
            row.update(status='implemented and verified for ordinary prepared registration',
                       ownership=fixes[row['boundary']],
                       test='actual failure enumeration: ordinary/immovable/pinned controls; default and donor importance; explicit successful lifecycle after each enumeration')
        if 'semaphore' in row['boundary']:
            row['test'] = 'Both actual allocations denied at each prepared Process1 + Thread4 semaphore position: 10 failures; each position also succeeds and retires; parent task counts restored.'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(scope='Ordinary prepared x86_64 Linux/glibc task/thread/semaphore acquisition and kernel-lane partial cleanup; no RPC/guest messages or activation.',
                  preserved_audit=dict(path=str(args.audit.resolve()), sha256=sha(args.audit)),
                  proof=dict(path=str(args.proof.resolve()), sha256=sha(args.proof), results=proof['results']),
                  boundaries=rows,
                  remaining_gates=[
                      'Legacy bootstrap and generic XNU no-fail acquisition APIs retain their original panic/failure contracts; checked ordinary APIs do not claim to fix those call sites.',
                      'MACF and other uncompiled optional configurations, non-x86_64 architectures, hidden libc internal allocation entry points and global process-wide OOM are unverified.',
                      'IPC port sets, lazy send-right construction, waiter/prepost allocation, guest fork/exec and cancellation semantics require distinct lifecycle proof; this proof covers initialization and retirement only.',
                      'Generation-specific C context ownership must be composed and validated separately before accepting retained old task/thread lifetime claims.',
                  ], universal_inner_oom_complete=False)
    (output / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(boundaries=len(rows), real_injected_failures=79, universal_inner_oom_complete=False)))


if __name__ == '__main__':
    main()
