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
    'patch_resource_worker_ownership.py',
    'patch_resource_work_handoff.py', 'build_resource_workqueue_configuration.py',
    'patch_resource_mutex_interlock.py',
    'patch_resource_dispatch_wakeup.py',
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
    'worker_ownership': 'work/prepared-registration/worker-ownership/proof3/verification.json',
    'work_handoff': 'work/resource-death-continuation/work-handoff/proof1/verification.json',
    'mutex_interlock': 'work/resource-death-continuation/mutex-interlock/proof1/verification.json',
    'dispatch_wakeup': 'work/resource-death-continuation/dispatch-wakeup/proof1/verification.json',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_build(root, report):
    build = json.loads((root/report).read_text())
    if not build['inputs_unchanged'] or build['resource_ownership_activated']:
        raise ValueError('invalid or activated owned candidate')
    if digest(Path(build['server'])) != build['server_sha256']:
        raise ValueError('owned server changed')
    if any(digest(Path(path)) != value for path,value in build['initial_input_sha256'].items()):
        raise ValueError('owned build source drift')
    if any(digest(Path(path)) != value for path,value in build['sha256'].items()):
        raise ValueError('owned build output drift')
    return build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--build-report', default='work/resource-death-continuation/server20-single/build.json')
    parser.add_argument('--guest-run', default='work/resource-death-continuation/guests20-single/run.json')
    parser.add_argument('--multi-build-report', default='work/resource-death-continuation/server21-multi/build.json')
    parser.add_argument('--multi-guest-run', default='work/resource-death-continuation/guests21-multi/run.json')
    parser.add_argument('--multi-observation', default='work/resource-death-continuation/multiworker-observation21/verification.json')
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    load = lambda name: json.loads((root/name).read_text())
    evidence = {key: dict(path=name, sha256=digest(root/name)) for key,name in EVIDENCE.items()}
    checked = load(EVIDENCE['checked_acquisition_and_generations'])['results']['checked']
    failures = sum(sum(checked[key]) for key in ('task_default_failures', 'task_inherited_importance_failures', 'thread_failures')) + checked['semaphore_all_positions_failures']
    preserved = load('work/resource-death-continuation/preserved-candidates.json')
    if any(digest(root/path) != value for path,value in preserved.items()):
        raise ValueError('original dirty candidate changed')
    base_path = 'work/resources-complete/server-build12/build.json'
    frozen_base = load(base_path)
    if any(digest(Path(path)) != value for path,value in frozen_base['sha256'].items()):
        raise ValueError('frozen preceding candidate changed')
    stopped_path = 'work/resource-death-continuation/stopped-wip-preservation3.json'
    stopped = load(stopped_path)
    if any(digest(root/path) != value for path,value in stopped['sha256'].items()):
        raise ValueError('stopped WIP changed since checkpoint')
    build_path = args.build_report
    build = checked_build(root, build_path)
    guest_path = args.guest_run
    guest_runs = load(guest_path)
    guest_count = 0
    guests = []
    guest_failures = []
    for run in guest_runs:
        report = Path(run['report'])
        data = json.loads(report.read_text())
        for variant in data['variants']:
            if not variant['kernel_mappings']: raise ValueError('missing actual kernel mapping')
            for case in variant['results']:
                if not case['normalized_pass']:
                    guest_failures.append(dict(group=run['group'], slot=variant['variant'], case=case['name'],
                        timed_out=case['launcher']['timed_out'], stderr=case['stderr']))
            guest_count += len(variant['results'])
        guests.append(dict(group=run['group'], report=str(report), sha256=digest(report), exit_code=run['exit_code']))
    multi_build = checked_build(root, args.multi_build_report)
    multi_configuration_path = Path(args.multi_build_report).parent/'workqueue-configuration.json'
    multi_configuration = load(multi_configuration_path)
    if multi_configuration['DSERVER_SINGLE_THREADED'] != 0 or multi_configuration['server_translation_units'] != 12:
        raise ValueError('missing multiworker compilation evidence')
    multi_observation = load(args.multi_observation)
    if multi_observation['server_sha256'] != multi_build['server_sha256'] or not multi_observation['multiple_workers_observed']:
        raise ValueError('missing actual multiworker observation')
    multi_runs = load(args.multi_guest_run)
    multi_cases, multi_failures = 0, []
    multi_reports = []
    for run in multi_runs:
        report = Path(run['report'])
        data = json.loads(report.read_text())
        multi_reports.append(dict(group=run['group'], path=str(report), sha256=digest(report), exit_code=run['exit_code']))
        for variant in data['variants']:
            if not variant['kernel_mappings']: raise ValueError('missing multiworker kernel mapping')
            for case in variant['results']:
                multi_cases += 1
                if not case['normalized_pass']:
                    multi_failures.append(dict(group=run['group'], slot=variant['variant'], case=case['name'],
                        timed_out=case['launcher']['timed_out']))
    python_log = root/'work/resource-death-continuation/python-tests-dispatch.log'
    if not python_log.read_text().rstrip().endswith('OK'): raise ValueError('Python checks failed')
    python_count = int(re.search(r'Ran (\d+) tests', python_log.read_text()).group(1))
    worker = load(EVIDENCE['worker_ownership'])
    if not worker['passed'] or not worker['inputs_unchanged']:
        raise ValueError('worker ownership validation failed')
    handoff = load(EVIDENCE['work_handoff'])
    if not handoff['passed'] or not handoff['inputs_unchanged']:
        raise ValueError('work handoff validation failed')
    mutex = load(EVIDENCE['mutex_interlock'])
    if not mutex['passed'] or not mutex['inputs_unchanged']:
        raise ValueError('mutex interlock validation failed')
    dispatch = load(EVIDENCE['dispatch_wakeup'])
    if not dispatch['passed'] or not dispatch['inputs_unchanged']:
        raise ValueError('dispatch wakeup validation failed')
    open_gates = [
        'The latest single-worker ordinary cohort fails thread-info.c after pthread_join; the assertion/termination ordering remains under review. Earlier server16 passed88 and is retained separately.',
        'Historical multiworker ordinary guest timeouts remain preserved. Scoped fresh cohort results and deterministic handoff/mutex regressions do not establish complete concurrency acceptance.',
        'Executing continuation closure ownership across suspension/native-worker reuse remains unverified; its attempted native validation was stopped by automatic cybersecurity review and was not retried or activated.',
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
        guests=dict(count=guest_count, expected_count=88, failures=guest_failures,
                    status='failing' if guest_failures or any(run['exit_code'] for run in guest_runs) else 'passed scoped ordinary cohort',
                    slots='same owned server/loader/kernel in both slots; not original-C versus Rust',
                    run=guest_path, run_sha256=digest(root/guest_path), cohorts=guests),
        multiworker=dict(status='failing' if multi_failures or any(run['exit_code'] for run in multi_runs) else 'passed scoped ordinary cohort',
            build_report=args.multi_build_report, build_report_sha256=digest(root/args.multi_build_report),
            server_sha256=multi_build['server_sha256'], inputs_unchanged=True,
            configuration=dict(path=str(multi_configuration_path), sha256=digest(root/multi_configuration_path)),
            observation=dict(path=args.multi_observation, sha256=digest(root/args.multi_observation)),
            guest_run=args.multi_guest_run, guest_run_sha256=digest(root/args.multi_guest_run),
            cases_executed=multi_cases, failures=multi_failures, cohorts=multi_reports),
        native_component_evidence=evidence,
        historical_checked_failure_cases=failures,
        historical_exec_failure_cases=load(EVIDENCE['historical_exec_core'])['results']['exec']['actual_allocation_failures'],
        native_results_scope='Each native derivative retains its own exact input hashes and binary; do not combine historical component proofs into a claim that the final binary passed the complete fault matrix.',
        python_check=dict(log=str(python_log), sha256=digest(python_log), command="python3 -m unittest discover -s tools -p 'test_*.py' -v", count=python_count),
        historical_multiworker_failures=[dict(
            build_report='work/resource-death-continuation/'+build_name+'/build.json',
            build_report_sha256=digest(root/'work/resource-death-continuation'/build_name/'build.json'),
            guest_run='work/resource-death-continuation/'+guest_name+'/run.json',
            guest_run_sha256=digest(root/'work/resource-death-continuation'/guest_name/'run.json'),
            status='failed; immutable earlier run') for build_name,guest_name in (
                ('server11-multiworker','guests11-multiworker'),('server13-multi','guests13-multi'),
                ('server17-multi','guests17-multi'),('server17-multi','guests19-plain-multi'))],
        historical_singleworker_success=dict(
            build_report='work/resource-death-continuation/server16-single/build.json',
            build_report_sha256=digest(root/'work/resource-death-continuation/server16-single/build.json'),
            guest_run='work/resource-death-continuation/guests16-single/run.json',
            guest_run_sha256=digest(root/'work/resource-death-continuation/guests16-single/run.json'),
            count=88, status='passed earlier scoped ordinary cohort'),
        thread_info_ordering=dict(
            review='work/resource-death-continuation/thread-info-ordering/source-review.json',
            review_sha256=digest(root/'work/resource-death-continuation/thread-info-ordering/source-review.json'),
            diagnostic='work/resource-death-continuation/thread-info-observed22/guests/comparison.json',
            diagnostic_sha256=digest(root/'work/resource-death-continuation/thread-info-observed22/guests/comparison.json'),
            scope='Read-only pinned-source ordering review; owned logging-only diagnostic preserves ESRCH assertion and passes two same-candidate cases. Original failing return/errno remain unknown; no expectation was weakened or failure resolved.'),
        preservation=dict(count=len(preserved), sha256=preserved,
            preceding_candidate=dict(report=base_path, sha256=digest(root/base_path),
                                     input_count=len(frozen_base['sha256']), inputs_unchanged=True),
            stopped_wip=dict(record=stopped_path, sha256=digest(root/stopped_path), count=len(stopped['sha256']))),
        automatic_review_stops=[dict(action='Exec-notification regressions/extra guest and cleanup-lane redesign follow-up', reason='Possible cybersecurity risk', status='stopped; unverified; not retried'),
                                dict(action='Direct native Checkin allocation/terminal-reply fault proof', reason='Possible cybersecurity risk', status='stopped; unverified; not retried'),
                                dict(action='Microthread continuation-lifetime ownership validation', reason='Possible cybersecurity risk', status='stopped; unverified; inactive WIP; not retried')],
        open_gates=open_gates)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(guest_executions=guest_count, historical_checked_failures=failures,
                          original_candidates_preserved=len(preserved), resource_complete=False)))


if __name__ == '__main__': main()
