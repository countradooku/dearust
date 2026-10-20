#!/usr/bin/env python3
"""Index current owned CPU delivery proofs, keeping incomplete resource gates open.

This validates identified saved runs; it does not execute tests or retry blocked
work. Historical revisions remain immutable and are never relabeled as current.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'work/resource-cpu-next'


def need(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def resolve(name):
    path = Path(name)
    return path if path.is_absolute() else ROOT/path


def check_hashes(values):
    for name, value in values.items():
        need(digest(resolve(name))==value, 'current input drift: '+name)


def reference(path):
    return dict(path=str(Path(path).relative_to(ROOT)), sha256=digest(path))


def checked_proof(path):
    data = load(path)
    need(data['passed'] is True and data['inputs_unchanged'] is True, 'proof incomplete: '+str(path))
    check_hashes(data['source_sha256'])
    compiled = {}
    for row in data['records']:
        need(row['exit_code']==0 and not row.get('timed_out'), 'failed proof record')
        command = row.get('command')
        if command and '-o' in command:
            compiled[row.get('variant', row.get('kind'))] = command[command.index('-o')+1]
        if 'binary_sha256' in row:
            if command:
                binary = command[command.index('-o')+1] if '-o' in command else command[0]
            else:
                binary = compiled[row['variant']]
            need(digest(binary)==row['binary_sha256'], 'proof binary changed')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'resource-cpu-current-index.json')
    args = parser.parse_args()
    component_path = WORK/'verification-v2.json'
    component = load(component_path)
    need(component['passed'] is True, 'CPU component incomplete')
    check_hashes(component['source_sha256'])
    for name, value in component['evidence_sha256'].items():
        need(digest(WORK/name)==value, 'CPU component evidence changed: '+name)
    proofs = {name:checked_proof(WORK/folder/'verification.json') for name, folder in
              [('engine', 'proof4'), ('query_failure', 'query-proof2'), ('lazy_adapter', 'lazy3')]}
    helper = ROOT/'tools/resource_cpu_dispatch.inc'
    build_path = WORK/'server2/server/build.json'
    build = load(build_path)
    need(build['inputs_unchanged'] is True and build['resource_ownership_activated'] is False, 'owned build incomplete')
    check_hashes(build['initial_input_sha256'])
    check_hashes(build['sha256'])
    server = Path(build['server'])
    need(digest(server)==build['server_sha256'], 'server bytes changed')
    need(digest(build_path.parent/'tools/resource-cpu-timer.inc')==digest(helper), 'server uses another CPU helper')
    configuration = load(build_path.parent/'cpu-dispatch-configuration.json')
    need(configuration['cpu_signal']==64 and configuration['receiver_deferred_until_first_arm'] is True,
         'unexpected receiver configuration')
    need(len(build['server_objects'])==12, 'incomplete server translation-unit rebuild')
    need(not any('DEARUST_RESOURCE_CPU_TEST' in str(command) for command in build['commands']), 'test hook in server')
    link_path = server.parent/'relink.json'
    link = load(link_path)
    for row in link['inputs']+link['server_inputs']:
        need(digest(row['selected'])==row['selected_sha256'], 'selected link input changed')
    for row in link['extra_archives']:
        need(digest(row['path'])==row['sha256'], 'selected archive changed')
    comparisons = []
    guest_count = 0
    kernel = ROOT/'work/resource-copy-next/matching-simd/libsystem_kernel-rust.dylib'
    for group in load(WORK/'guests2/run.json'):
        need(group['exit_code']==0, 'guest cohort failed')
        path = Path(group['report'])
        data = load(path)
        for variant in data['variants']:
            need(variant['kernel_mappings'] and variant['kernel_sha256']==digest(kernel)
                 and variant['staged_kernel_sha256']==digest(kernel), 'guest kernel provenance mismatch')
            for case in variant['results']:
                need(case['strict_pass'] is True, 'ordinary guest failure')
                guest_count += 1
        comparisons.append(dict(group=group['group'], **reference(path)))
    need(guest_count==88, 'guest count mismatch')
    observation_path = WORK/'guest-observations2.json'
    observation = load(observation_path)
    need(observation['exit_code']==0 and observation['expected']['darlingserver']==digest(server), 'server observation mismatch')
    observed = {}
    for row in observation['records']:
        need(row['sha256']==observation['expected'][row['name']] and row['maps'], 'mapped executable hash mismatch')
        observed[row['name']] = observed.get(row['name'], 0)+1
    need(observed.get('darlingserver', 0)==10 and observed.get('mldr', 0)>0, 'missing mapped server/loader evidence')
    benchmark_path = WORK/'benchmark3/verification.json'
    benchmark = load(benchmark_path)
    need(benchmark['inputs_unchanged'] is True and len(benchmark['records'])==42, 'CPU benchmark incomplete')
    check_hashes(benchmark['source_sha256'])
    for row in benchmark['binaries'].values():
        need(digest(row['path'])==row['sha256'], 'benchmark executable changed')
    need(all(row['exit_code']==0 for row in benchmark['records']), 'CPU benchmark failure')
    startup_path = WORK/'startup3/verification.json'
    startup = load(startup_path)
    need(startup['passed'] is True and startup['inputs_unchanged'] is True, 'startup comparison incomplete')
    check_hashes(startup['tool_sha256'])
    for row in startup['variants'].values():
        check_hashes(row['preserved_sha256'])
        check_hashes(row['generated_source_sha256'])
        need(digest(row['binary'])==row['binary_sha256'], 'startup binary changed')
    preservation = {}
    for name in ['preserved-candidates.json', 'stopped-wip-preservation4.json']:
        path = ROOT/'work/resource-death-continuation'/name
        values = load(path)
        values = values.get('sha256', values)
        check_hashes(values)
        preservation[name] = dict(**reference(path), files=len(values), unchanged=True)
    frozen_path = ROOT/'work/resources-complete/server-build12/build.json'
    frozen = load(frozen_path)
    check_hashes(frozen['sha256'])
    preservation['frozen_server12'] = dict(**reference(frozen_path), files=len(frozen['sha256']), unchanged=True)
    previous_runtime_path = ROOT/'work/resource-copy-next/final-preservation.json'
    previous_runtimes = load(previous_runtime_path)['base_runtime_files']
    for row in previous_runtimes.values():
        need(digest(resolve(row['path']))==row['sha256'], 'preceding paired runtime changed')
    preservation['preceding_paired_runtimes'] = dict(manifest=reference(previous_runtime_path),
        files=previous_runtimes, unchanged=True)
    v1_manifest = WORK/'revision1-frozen/manifest.json'
    for name, value in load(v1_manifest).items():
        need(digest(v1_manifest.parent/name)==value, 'frozen first-revision source changed')
    previous_build = load(WORK/'server1/server/build.json')
    previous_owned = {name:value for name,value in previous_build['sha256'].items()
                      if resolve(name).is_relative_to(WORK/'server1/server')}
    check_hashes(previous_owned)
    preservation['historical_cpu_revision1'] = dict(source_manifest=reference(v1_manifest),
        owned_server_files=len(previous_owned), unchanged=True,
        scope='First revision remains historical; its mutable repository source references legitimately drift to revision2.')
    python_path = WORK/'python-tests-final.log'
    python_text = python_path.read_text()
    need(python_text.rstrip().endswith('OK'), 'Python checks did not pass')
    count = int(re.search(r'Ran (\d+) tests', python_text)[1])
    sources = {str(path.relative_to(ROOT)):digest(path) for path in [Path(__file__).resolve(),
        ROOT/'tools/build_resource_cpu_dispatch_server.py', ROOT/'tools/check_resource_cpu_lazy.py',
        ROOT/'tools/fixtures/resource-cpu-lazy-native.cpp', ROOT/'tools/benchmark_resource_cpu_dispatch.py',
        ROOT/'tools/fixtures/resource-cpu-dispatch-bench.cpp', ROOT/'tools/benchmark_resource_cpu_startup.py']}
    sources.update({str(resolve(name).relative_to(ROOT)):value for name,value in component['source_sha256'].items()
                    if resolve(name).is_relative_to(ROOT)})
    record = dict(schema_version=1,
        scope='Owned Linux-host CPU-delivery prerequisite, integrated into an inactive single-worker server derivative. No Rust source replacement or whole-resource completion.',
        implemented_checks_passed=True, resource_complete=False, resource_ownership_activated=False,
        blocked_checks_retried=False, performance_gate_passed=False, multiworker_gate_passed=False,
        source_sha256=sources, component=reference(component_path),
        proofs={name:reference(WORK/folder/'verification.json') for name,folder in [('engine','proof4'),('query_failure','query-proof2'),('lazy_adapter','lazy3')]},
        server=dict(**reference(server), build=reference(build_path), link=reference(link_path),
            translation_units=12, signal=64, receiver_deferred_until_first_arm=True,
            scope='Single-worker owned build; no current multiworker acceptance or production activation.'),
        guests=dict(strict_passes=guest_count, comparisons=comparisons, observation=reference(observation_path),
            observed_executables=observed, kernel=reference(kernel),
            slots='Same owned server2/loader12/SIMD kernel in both slots; not original C versus Rust.'),
        performance=dict(native_benchmark=reference(benchmark_path), native_summary=benchmark['summary'],
            startup_benchmark=reference(startup_path), startup_summary=startup['summary'],
            first_revision_benchmark=reference(WORK/'benchmark2/verification.json'),
            limitations=['Native engine workloads only; no whole application or resource-performance acceptance.',
                'Cold first arm and explicit teardown have additional preparation/lifetime cost; two service FDs and default stack remain visible.',
                'VmSize/VmData, RSS and per-executable VmHWM are distinct. Raw RUSAGE_SELF maxrss may retain pre-exec high water.',
                'Native generic-copy heap/cross-page CPU remains materially above C, an independent open gate.']),
        python=dict(**reference(python_path), count=count, exit_code=0),
        rust_scope='No Rust source changes in this batch; prior214 ordinary/11compilefail and Darwin ABI checks retain their exact historical scope in resource-copy-current-index.json.',
        preservation=preservation,
        open_gates=['Automatic-review-stopped wait-admission fix/check remains inactive; last historical multiworker78/80 has two Mach lockset timeouts. No blocked checks retried.',
            'Earlier RPC, Checkin, exec-notification/cleanup-lane and executing continuation validations remain stopped.',
            'Full unsigned ownership and VM/wiring, host hard-limit coherence/partial effects, credential/NPROC, fork/exec/cancellation/shutdown and architecture gates remain unfinished.',
            'Signal reservation64 is explicit for this owned native server; wider configuration/platform support is not established.',
            'A timer-state query failure remains a reported negative diagnostic until explicit successful rearm; no automatic redelivery or retry is claimed.',
            'Host C++ prerequisite repair is not a completed Rust implementation or legacy retirement.'])
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(guest_passes=guest_count, python=count, source_files=len(sources), resource_complete=False)))


if __name__ == '__main__':
    main()
