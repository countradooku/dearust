#!/usr/bin/env python3
"""Index scoped lifecycle evidence without rewriting any historical run."""
import argparse
import hashlib
import json
import re
from pathlib import Path


SOURCES = [
    'build_resource_lifecycle_server.py', 'patch_resource_death_work.py',
    'patch_resource_death_lifecycle.py', 'patch_resource_state_death.py',
    'patch_prepared_resource_lane_cost.py', 'patch_prepared_resource_inner_dtape.py',
    'patch_prepared_resource_checked_ipc.py', 'patch_resource_kernel_contexts.py',
    'patch_resource_generation_resume.py', 'patch_resource_prepared_exec.py',
    'resource-prepared-exec.inc', 'patch_prepared_resource_process_notifications.py',
    'prepared-resource-process-notifications.inc', 'patch_prepared_resource_machport_retirement.py',
    'patch_prepared_resource_mach_waiter.py', 'patch_prepared_resource_kq_ownership.py',
    'patch_prepared_resource_mach_notifications.py', 'prepared-resource-mach-notifications.inc',
    'patch_prepared_resource_replies.py', 'prepared-resource-reply-queue.inc',
    'prepared-resource-reply-send.inc',
]

EVIDENCE = {
    'inner_acquisition': 'work/resources-complete/prepared-inner-dtape-proof3/verification.json',
    'checked_acquisition_and_generations': 'work/resources-complete/prepared-checked-ipc-proof9/verification.json',
    'historical_exec_core': 'work/resources-complete/prepared-checked-ipc-proof11/verification.json',
    'mach_callback_retirement': 'work/resource-death-continuation/actual-machport8/verification.json',
    'resource_state': 'work/prepared-registration/resource-state-death/actual3/verification.json',
    'generation_resume': 'work/prepared-registration/generation-resume/proof3/verification.json',
    'reserved_work_queue': 'work/resource-death-continuation/death-work-proof3/verification.json',
    'reply_queue': 'work/resource-death-continuation/replies8/verification.json',
    'sustained_cleanup': 'work/prepared-registration/lane-cost/actual-cached-storage-mutex1/verification.json',
    'historical_startup': 'work/prepared-registration/lane-cost/matched-startup2/verification.json',
    'earlier_ordinary_process_kqueue': 'work/resource-death-continuation/process-kqueue-guest4/comparison.json',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    load = lambda name: json.loads((root/name).read_text())
    evidence = {key: dict(path=name, sha256=digest(root/name)) for key,name in EVIDENCE.items()}
    checked = load(EVIDENCE['checked_acquisition_and_generations'])['results']['checked']
    failures = sum(sum(checked[key]) for key in ('task_default_failures', 'task_inherited_importance_failures', 'thread_failures')) + checked['semaphore_all_positions_failures']
    preserved = load('work/resource-death-continuation/preserved-candidates.json')
    if any(digest(root/path) != value for path,value in preserved.items()):
        raise ValueError('original dirty candidate changed')
    build_path = 'work/resource-death-continuation/server8/build.json'
    build = load(build_path)
    if digest(Path(build['server'])) != build['server_sha256']:
        raise ValueError('owned server changed')
    if any(digest(Path(path)) != value for path,value in build['initial_input_sha256'].items()):
        raise ValueError('owned build source drift')
    guest_path = 'work/resource-death-continuation/guests8/run.json'
    guest_runs = load(guest_path)
    guest_count = 0
    guests = []
    for run in guest_runs:
        if run['exit_code']: raise ValueError('ordinary cohort failed')
        report = Path(run['report'])
        data = json.loads(report.read_text())
        for variant in data['variants']:
            if not variant['kernel_mappings']: raise ValueError('missing actual kernel mapping')
            if not all(case['normalized_pass'] for case in variant['results']):
                raise ValueError('ordinary guest result failed')
            guest_count += len(variant['results'])
        guests.append(dict(group=run['group'], report=str(report), sha256=digest(report)))
    python_log = root/'work/resource-death-continuation/python-tests2.log'
    if not python_log.read_text().rstrip().endswith('OK'): raise ValueError('Python checks failed')
    python_count = int(re.search(r'Ran (\d+) tests', python_log.read_text()).group(1))
    open_gates = [
        'Latest prepared exec lock-scope changes pass ordinary guest execution; the earlier29-failure exec proof is historical and does not validate those later changes.',
        'Direct native Checkin fault proof and further exec-notification validation stopped by automatic cybersecurity review; do not retry, rephrase or transfer those actions.',
        'Original adversarial RPC verification remains externally blocked and was not retried.',
        'Complete initial-call failure behavior, Process/Thread active-call cancellation and full shutdown draining remain unverified.',
        'Legacy bootstrap and generic no-fail XNU APIs, lazy IPC/port-set/prepost/waiter acquisition, optional MACF/configurations and other architectures remain open.',
        'A pendingReplacement notification follows physical exec; old-task VM access requires a separate pre-exec drain/cancellation protocol.',
        'Native cleanup retains one additional default-stack pthread; broader performance acceptance remains open. The stopped follow-up was not retried.',
        'Unsigned resource ownership, host hard-limit/VM coherence and partial effects, credential/NPROC authority, fork/exec/signal/cancellation and generic-copy CPU regressions remain unfinished.',
        'Resources and packages01-09 remain unfinished; no whole target replacement, source replacement count or legacy retirement follows from these C/C++ prerequisites.',
    ]
    record = dict(schema_version=1, scope='Owned resource lifecycle prerequisite candidate; scoped evidence index, not a full Rust port or whole-runtime OOM/performance acceptance.',
        resource_complete=False, resource_ownership_activated=False, blocked_rpc_checks_retried=False,
        source_sha256={str(Path('tools')/name): digest(root/'tools'/name) for name in SOURCES},
        owned_build=dict(report=build_path, report_sha256=digest(root/build_path), server=build['server'],
                         server_sha256=build['server_sha256'], inputs_unchanged=True,
                         complete_combined_fault_validation=False),
        guests=dict(count=guest_count, slots='same owned server/loader/kernel in both slots; not original-C versus Rust',
                    run=guest_path, run_sha256=digest(root/guest_path), cohorts=guests),
        native_component_evidence=evidence,
        historical_checked_failure_cases=failures,
        historical_exec_failure_cases=load(EVIDENCE['historical_exec_core'])['results']['exec']['actual_allocation_failures'],
        native_results_scope='Each native derivative retains its own exact input hashes and binary; do not combine historical component proofs into a claim that the final binary passed the complete fault matrix.',
        python_check=dict(log=str(python_log), sha256=digest(python_log), command="python3 -m unittest discover -s tools -p 'test_*.py' -v", count=python_count),
        preservation=dict(count=len(preserved), sha256=preserved),
        automatic_review_stops=[dict(action='Exec-notification regressions/extra guest and cleanup-lane redesign follow-up', reason='Possible cybersecurity risk', status='stopped; unverified; not retried'),
                                dict(action='Direct native Checkin allocation/terminal-reply fault proof', reason='Possible cybersecurity risk', status='stopped; unverified; not retried')],
        open_gates=open_gates)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(guest_executions=guest_count, historical_checked_failures=failures,
                          original_candidates_preserved=len(preserved), resource_complete=False)))


if __name__ == '__main__': main()
