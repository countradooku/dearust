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
    'patch_resource_dispatch_wakeup.py', 'patch_resource_impersonation_handoff.py',
    'patch_resource_fork_signal.py', 'patch_resource_kernel_async_submission.py',
    'patch_resource_resume_consumption.py', 'patch_resource_registration_lock_scope.py',
    'patch_resource_xnu_wait_state.py', 'build_resource_lockset_server.py',
    'patch_resource_bootstrap_thread_storage.py', 'patch_resource_wait_result.py',
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
    'impersonation_handoff': 'work/resource-death-continuation/impersonation-handoff/proof4/verification.json',
    'fork_signal': 'work/resource-death-continuation/fork-signal/proof1/verification.json',
    'thread_info_contract': 'work/resource-death-continuation/thread-info-ordering/verification.json',
    'kernel_async_submission': 'work/resource-death-continuation/kernel-async-submission/proof3/verification.json',
    'resume_consumption': 'work/resource-death-continuation/resume-consumption/proof2/verification.json',
    'registration_lock_scope': 'work/resource-death-continuation/registration-lock-scope/proof5/verification.json',
    'xnu_wait_state': 'work/resource-death-continuation/xnu-wait-state/proof3/verification.json',
    'rust_lockset': 'work/resource-death-continuation/lockset-registry/proof6/verification.json',
    'bootstrap_publication_storage': 'work/resource-death-continuation/bootstrap-thread-storage/proof2/verification.json',
    'wait_result': 'work/resource-death-continuation/wait-result/proof2/verification.json',
    'lockset_cancellation': 'work/resource-death-continuation/lockset-cancel/proof1/verification.json',
}

LOCKSET_SOURCES = (
    'crates/dearust-lockset/src/lib.rs', 'crates/dearust-lockset/src/core.rs',
    'crates/dearust-lockset/src/tests.rs', 'crates/dearust-lockset/bridge/sync_lock.c',
    'crates/dearust-lockset/SAFETY.txt',
)


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


def checked_lockset(root, report, build):
    """Trace the selected Rust archive and C boundary through this exact link.

    These checks establish provenance, not concurrency acceptance. Historical
    archives/reports are not rewritten or given the current build's verdict.
    """
    server_dir = (root / report).parent
    rust_path = server_dir.parent / 'rust-build.json'
    configuration_path = server_dir / 'lockset-configuration.json'
    c_build_path = server_dir / 'lifecycle-c-build.json'
    relink_path = server_dir / 'linked/relink.json'
    rust = json.loads(rust_path.read_text())
    configuration = json.loads(configuration_path.read_text())
    c_build = json.loads(c_build_path.read_text())
    relink = json.loads(relink_path.read_text())

    def reference(path, expected=None):
        path = root / path
        value = digest(path)
        if expected is not None and value != expected:
            raise ValueError('selected lockset provenance drift: ' + str(path))
        return dict(path=str(path), sha256=value)

    def recorded_hash(mapping, path):
        matches = [value for name, value in mapping.items() if root / name == path]
        if len(matches) != 1:
            raise ValueError('missing or duplicate selected lockset input: ' + str(path))
        return matches[0]

    rust_reference = reference(rust_path, recorded_hash(build['initial_input_sha256'], rust_path))
    configuration_reference = reference(configuration_path, recorded_hash(build['sha256'], configuration_path))
    if rust['resource_ownership_activated'] or configuration['resource_ownership_activated']:
        raise ValueError('activated lockset candidate')
    for key in ('source_sha256', 'archive', 'archive_sha256', 'command', 'rustc'):
        if rust[key] != configuration[key]:
            raise ValueError('Rust build and selected lockset configuration disagree: ' + key)
    for name, value in rust['source_sha256'].items():
        path = root / name
        reference(path, value)
        if recorded_hash(build['initial_input_sha256'], path) != value:
            raise ValueError('lockset source disagrees with consumer build: ' + name)
    for name in LOCKSET_SOURCES[:3]:
        if recorded_hash(rust['source_sha256'], root / name) != digest(root / name):
            raise ValueError('current Rust lockset source missing from selected build: ' + name)

    archive_path = root / rust['archive']
    archive_reference = reference(archive_path, rust['archive_sha256'])
    if recorded_hash(build['initial_input_sha256'], archive_path) != rust['archive_sha256']:
        raise ValueError('selected Rust archive disagrees with consumer build')
    selected_archives = [item for item in relink['extra_archives']
                         if root / item['path'] == archive_path]
    lockset_archives = [item for item in relink['extra_archives']
                       if Path(item['path']).name in ('libdarling_lockset.a', 'libdearust_lockset.a')]
    if (len(selected_archives) != 1 or len(lockset_archives) != 1
            or selected_archives[0]['sha256'] != rust['archive_sha256']
            or str(archive_path) not in relink['link_argv']):
        raise ValueError('selected Rust archive absent from actual consumer link')
    if relink['server_sha256'] != build['server_sha256']:
        raise ValueError('lockset relink and build server hashes disagree')

    bridge_path = root / configuration['selected_bridge']
    if bridge_path != root / 'crates/dearust-lockset/bridge/sync_lock.c':
        raise ValueError('unexpected selected lockset C boundary')
    bridge_reference = reference(bridge_path, configuration['selected_bridge_sha256'])
    if recorded_hash(rust['source_sha256'], bridge_path) != bridge_reference['sha256']:
        raise ValueError('C boundary disagrees with recorded lockset input')
    bridge_inputs = [item for item in relink['inputs']
                     if item['source'] == 'xnu/osfmk/kern/sync_lock.c']
    if len(bridge_inputs) != 1 or bridge_inputs[0]['replaced'] is not True:
        raise ValueError('missing selected lockset C boundary object')
    bridge_object = bridge_inputs[0]
    bridge_object_path = root / bridge_object['selected']
    bridge_object_reference = reference(bridge_object_path, bridge_object['selected_sha256'])
    bridge_key = 'xnu/osfmk/kern/sync_lock.c'
    compiled_bridge_path = root / c_build['compiled_sources'][bridge_key]
    compiled_bridge_reference = reference(compiled_bridge_path, bridge_reference['sha256'])
    if (root / c_build['selected_sources'][bridge_key] != bridge_path
            or recorded_hash(build['sha256'], compiled_bridge_path) != bridge_reference['sha256']
            or not any('-c' in command and '-o' in command
                and command[command.index('-c') + 1] == str(compiled_bridge_path)
                and command[command.index('-o') + 1] == str(bridge_object_path)
                for command in c_build['commands'])):
        raise ValueError('selected C boundary lacks matching source/compiler provenance')
    if (recorded_hash(build['sha256'], bridge_object_path) != bridge_object['selected_sha256']
            or str(bridge_object_path) not in relink['archive_argv']):
        raise ValueError('selected C boundary absent from consumer archive')
    consumer_archive_path = root / relink['archive_argv'][2]
    consumer_archive_reference = reference(consumer_archive_path,
        recorded_hash(build['sha256'], consumer_archive_path))
    if str(consumer_archive_path) not in relink['link_argv']:
        raise ValueError('selected consumer archive absent from link')
    map_path = root / relink['link_map']
    if '-Wl,-Map,' + str(map_path) not in relink['link_argv']:
        raise ValueError('consumer map path disagrees with link command')
    map_text = map_path.read_text()
    if (str(archive_path) + '(' not in map_text
            or str(consumer_archive_path) + '(' + bridge_object_path.name + ')' not in map_text):
        raise ValueError('consumer map lacks Rust archive or selected C boundary member')
    return dict(scope='Candidate-only selected archive and C boundary provenance; not complete concurrency, ownership or runtime acceptance.',
        resource_ownership_activated=False,
        build_report=reference(root / report), rust_build=rust_reference,
        configuration=configuration_reference, archive=archive_reference,
        bridge_source=bridge_reference, bridge_object=bridge_object_reference,
        compiled_bridge_source=compiled_bridge_reference,
        c_build=reference(c_build_path, recorded_hash(build['sha256'], c_build_path)),
        consumer_archive=consumer_archive_reference, relink=reference(relink_path),
        link_map=reference(map_path), server=reference(Path(build['server']), build['server_sha256']),
        source_sha256=rust['source_sha256'], command=rust['command'], rustc=rust['rustc'],
        preceding_archive=reference(Path(configuration['preceding_archive']),
                                    configuration['preceding_archive_sha256']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--build-report', required=True)
    parser.add_argument('--guest-run', required=True)
    parser.add_argument('--multi-build-report', required=True)
    parser.add_argument('--multi-guest-run', required=True)
    parser.add_argument('--multi-observation', required=True)
    parser.add_argument('--rust-check-log', default='work/resource-death-continuation/cargo-lockset56-check.log')
    parser.add_argument('--rust-check-command', default='CARGO_TARGET_DIR=work/resource-death-continuation/cargo-lockset44 cargo xtask check')
    parser.add_argument('--python-check-log', default='work/resource-death-continuation/python-tests-fork33.log')
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
    stopped_path = 'work/resource-death-continuation/stopped-wip-preservation4.json'
    stopped = load(stopped_path)
    if any(digest(root/path) != value for path,value in stopped['sha256'].items()):
        raise ValueError('stopped WIP changed since checkpoint')
    build_path = args.build_report
    build = checked_build(root, build_path)
    single_lockset = checked_lockset(root, build_path, build)
    guest_path = args.guest_run
    guest_runs = load(guest_path)
    guest_count = 0
    guests = []
    guest_failures = []
    for run in guest_runs:
        report = Path(run['report'])
        data = json.loads(report.read_text())
        for variant in data['variants']:
            if not variant['kernel_mappings'] and any(case['normalized_pass'] for case in variant['results']):
                raise ValueError('successful guest lacks actual kernel mapping')
            for case in variant['results']:
                if not case['normalized_pass']:
                    guest_failures.append(dict(group=run['group'], slot=variant['variant'], case=case['name'],
                        timed_out=case['launcher']['timed_out'], stderr=case['stderr'],
                        launcher_stderr=case['launcher']['stderr'], launcher_stdout=case['launcher']['stdout']))
            guest_count += len(variant['results'])
        guests.append(dict(group=run['group'], report=str(report), sha256=digest(report), exit_code=run['exit_code']))
    multi_build = checked_build(root, args.multi_build_report)
    multi_lockset = checked_lockset(root, args.multi_build_report, multi_build)
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
            if not variant['kernel_mappings'] and any(case['normalized_pass'] for case in variant['results']):
                raise ValueError('successful multiworker guest lacks actual kernel mapping')
            for case in variant['results']:
                multi_cases += 1
                if not case['normalized_pass']:
                    multi_failures.append(dict(group=run['group'], slot=variant['variant'], case=case['name'],
                        timed_out=case['launcher']['timed_out'], stderr=case['stderr'],
                        launcher_stderr=case['launcher']['stderr'], launcher_stdout=case['launcher']['stdout']))
    python_log = root/args.python_check_log
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
    for name in ('impersonation_handoff', 'fork_signal', 'thread_info_contract',
                 'kernel_async_submission', 'resume_consumption', 'registration_lock_scope',
                 'xnu_wait_state', 'rust_lockset', 'lockset_cancellation', 'bootstrap_publication_storage', 'wait_result'):
        if not load(EVIDENCE[name])['passed']:
            raise ValueError('failed focused component proof: ' + name)
    lockset_proof = load(EVIDENCE['rust_lockset'])
    if not lockset_proof['inputs_unchanged']:
        raise ValueError('Rust lockset focused proof inputs changed during verification')
    for name in LOCKSET_SOURCES:
        if lockset_proof['input_sha256'].get(name) != digest(root/name):
            raise ValueError('Rust lockset focused proof does not cover current input: ' + name)
    cancellation_proof = load(EVIDENCE['lockset_cancellation'])
    cancellation_bridge = str(root/'crates/dearust-lockset/bridge/sync_lock.c')
    if (not cancellation_proof['inputs_unchanged'] or
            cancellation_proof['input_sha256'].get(cancellation_bridge) != digest(Path(cancellation_bridge))):
        raise ValueError('lockset cancellation proof does not cover current C boundary')
    open_gates = [
        'Historical multiworker failures remain preserved. Passing scoped ordinary cohorts and deterministic handoff/mutex/fork-signal regressions do not establish complete concurrency acceptance.',
        'Impersonated C executions and the original target share one C wait identity; reverse wake attribution remains unresolved and no reverse routing is activated.',
        'Generic Registry and task-pin native proofs do not replace complete actual initial-call/cancellation/combined-fault validation.',
        'Actual trace56 confirms duplicate insertion of the same C mutex waiter after an unrelated XNU wake. Multiworker remains failing. The delegated wait-admission fix/regression was stopped by automatic cybersecurity review; its three WIP files remain inactive and were not retried or transferred.',
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
    rust_log = root/args.rust_check_log
    rust_text = rust_log.read_text()
    rust_passed = sum(map(int, re.findall(r'test result: ok\. (\d+) passed;', rust_text)))
    rust_compile_fail = rust_text.count('compile fail ... ok')
    if not rust_passed or '... FAILED' in rust_text:
        raise ValueError('Rust workspace checks failed')
    record = dict(schema_version=1, scope='Owned resource lifecycle prerequisite candidate; scoped evidence index, not a full Rust port or whole-runtime OOM/performance acceptance.',
        resource_complete=False, resource_ownership_activated=False, blocked_rpc_checks_retried=False,
        source_sha256={str(Path('tools')/name): digest(root/'tools'/name) for name in SOURCES},
        owned_build=dict(report=build_path, report_sha256=digest(root/build_path), server=build['server'],
                         server_sha256=build['server_sha256'], inputs_unchanged=True,
                         complete_combined_fault_validation=False),
        guests=dict(count=guest_count, expected_count=88, failures=guest_failures,
                    status=('failing' if guest_failures or any(run['exit_code'] for run in guest_runs) else
                            'passed scoped ordinary cohort' if guest_count == 88 else 'incomplete scoped ordinary cohort'),
                    slots='same owned server/loader/kernel in both slots; not original-C versus Rust',
                    run=guest_path, run_sha256=digest(root/guest_path), cohorts=guests),
        multiworker=dict(status=('failing' if multi_failures or any(run['exit_code'] for run in multi_runs) else
                                'passed scoped ordinary cohort' if multi_cases == 88 else 'incomplete scoped ordinary cohort'),
            build_report=args.multi_build_report, build_report_sha256=digest(root/args.multi_build_report),
            server_sha256=multi_build['server_sha256'], inputs_unchanged=True,
            configuration=dict(path=str(multi_configuration_path), sha256=digest(root/multi_configuration_path)),
            observation=dict(path=args.multi_observation, sha256=digest(root/args.multi_observation)),
            guest_run=args.multi_guest_run, guest_run_sha256=digest(root/args.multi_guest_run),
            cases_executed=multi_cases, failures=multi_failures, cohorts=multi_reports),
        native_component_evidence=evidence,
        rust_lockset=dict(source_sha256={name: digest(root/name) for name in LOCKSET_SOURCES},
            selected_builds=dict(single=single_lockset, multi=multi_lockset),
            workspace_check=dict(log=str(rust_log), sha256=digest(rust_log),
                ordinary=rust_passed-rust_compile_fail, compile_fail=rust_compile_fail,
                command=args.rust_check_command)),
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
                ('server17-multi','guests17-multi'),('server17-multi','guests19-plain-multi'),
                ('server21-multi','guests21-multi'),('server30-multi','guests30-multi'),
                ('server33-multi','guests33-multi'),('server39-multi','guests39-multi'),
                ('lockset41-multi/server','guests41-multi'),('lockset45-multi/server','guests45-multi'),
                ('lockset49-multi/server','guests49-multi'))],
        historical_singleworker_success=dict(
            build_report='work/resource-death-continuation/server16-single/build.json',
            build_report_sha256=digest(root/'work/resource-death-continuation/server16-single/build.json'),
            guest_run='work/resource-death-continuation/guests16-single/run.json',
            guest_run_sha256=digest(root/'work/resource-death-continuation/guests16-single/run.json'),
            count=88, status='passed earlier scoped ordinary cohort'),
        actual_failure_traces={name: dict(path=path, sha256=digest(root/path)) for name,path in {
            'nested_wait_cancellation': 'work/resource-death-continuation/wait-trace53/finding.json',
            'duplicate_mutex_waiter': 'work/resource-death-continuation/wait-trace56/finding.json'}.items()},
        thread_info_ordering=dict(
            review='work/resource-death-continuation/thread-info-ordering/source-review.json',
            review_sha256=digest(root/'work/resource-death-continuation/thread-info-ordering/source-review.json'),
            verification=EVIDENCE['thread_info_contract'],
            verification_sha256=digest(root/EVIDENCE['thread_info_contract']),
            fixture_revision='work/resource-death-continuation/retained-probes29/revision.json',
            fixture_revision_sha256=digest(root/'work/resource-death-continuation/retained-probes29/revision.json'),
            scope='Pinned ordering and forced native-exit delay reproduce invalid immediate-ESRCH assertion with112-byte success. Corrected fixture validates live records/bounds until eventual ESRCH with untouched output; forced and normal cases pass. Original source, executable and failing reports remain preserved.'),
        preservation=dict(count=len(preserved), sha256=preserved,
            preceding_candidate=dict(report=base_path, sha256=digest(root/base_path),
                                     input_count=len(frozen_base['sha256']), inputs_unchanged=True),
            stopped_wip=dict(record=stopped_path, sha256=digest(root/stopped_path), count=len(stopped['sha256']))),
        automatic_review_stops=[dict(action='Exec-notification regressions/extra guest and cleanup-lane redesign follow-up', reason='Possible cybersecurity risk', status='stopped; unverified; not retried'),
                                dict(action='Direct native Checkin allocation/terminal-reply fault proof', reason='Possible cybersecurity risk', status='stopped; unverified; not retried'),
                                dict(action='Microthread continuation-lifetime ownership validation', reason='Possible cybersecurity risk', status='stopped; unverified; inactive WIP; not retried'),
                                dict(action='Intrusive C queue wait-admission fix and regression', reason='Possible cybersecurity risk', status='stopped; unverified; three inactive WIP files; not retried or transferred')],
        open_gates=open_gates)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(guest_executions=guest_count, historical_checked_failures=failures,
                          original_candidates_preserved=len(preserved), resource_complete=False)))


if __name__ == '__main__': main()
