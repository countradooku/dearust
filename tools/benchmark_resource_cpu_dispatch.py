#!/usr/bin/env python3
"""Compare exact previous/new CPU helpers in balanced owned native processes.

Measures cold first arm, repeated future rearm, real one-shot delivery CPU tails,
process CPU, RSS/address space, threads and FDs. These are native engine scopes,
not guest/application or complete resource acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def distribution(values):
    q1, _, q3 = statistics.quantiles(values, n=4, method='inclusive') if len(values) > 1 else (values[0],)*3
    ordered = sorted(values)
    def percentile(fraction):
        position = (len(ordered)-1)*fraction
        low = int(position)
        high = min(low+1, len(ordered)-1)
        return ordered[low] + (ordered[high]-ordered[low])*(position-low)
    return dict(samples=len(values), median=statistics.median(values), q1=q1, q3=q3,
                p95=percentile(0.95), p99=percentile(0.99), minimum=min(values), maximum=max(values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=21)
    args = parser.parse_args()
    if args.samples < 3:
        parser.error('at least three balanced fresh processes per variant are required')
    repo = Path(__file__).resolve().parents[1]
    previous, current = args.previous.resolve(strict=True), args.current.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or any(output.is_relative_to(p.parent) for p in (previous, current)):
        parser.error('output must be absent and disjoint from both helper directories')
    fixture = repo/'tools/fixtures/resource-cpu-dispatch-bench.cpp'
    sources = [Path(__file__).resolve(), fixture, previous, current]
    initial = {str(path): digest(path) for path in sources}
    output.mkdir(parents=True)
    commands, binaries = [], {}
    records = []
    try:
        for variant, helper in [('previous', previous), ('current', current)]:
            binary = output/variant
            command = ['clang++', '-std=c++17', '-O2', '-g', '-pthread',
                       '-DDEARUST_CPU_HELPER=' + json.dumps(str(helper)),
                       '-DDEARUST_CPU_DISPATCH=' + str(int(variant == 'current')),
                       str(fixture), '-o', str(binary), '-lrt']
            subprocess.run(command, check=True)
            commands.append(command)
            binaries[variant] = dict(path=str(binary), sha256=digest(binary))
        # Both receiver and child inherit this same allowed CPU set. No hidden
        # affinity to a different core separates worker cost from engine CPU.
        cpus = sorted(os.sched_getaffinity(0))
        selected = cpus[:2]
        for sample in range(args.samples):
            order = ('previous', 'current') if sample % 2 == 0 else ('current', 'previous')
            for variant in order:
                command = ['taskset', '-c', ','.join(map(str, selected)), binaries[variant]['path']]
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                record = dict(sample=sample, variant=variant, command=command, exit_code=result.returncode,
                              stdout=result.stdout, stderr=result.stderr)
                records.append(record)
                if result.returncode:
                    raise ValueError('CPU helper benchmark failed: ' + variant)
                record['measurements'] = json.loads(result.stdout)
                (output/'raw.json').write_text(json.dumps(records, indent=2)+'\n')
            print(json.dumps(dict(sample=sample+1, per_variant=args.samples)), flush=True)
        summary = {}
        scalars = ['setup_cpu_ns', 'setup_wall_ns', 'rearm_cpu_ns', 'rearm_wall_ns',
                   'expiry_cpu_ns', 'expiry_wall_ns', 'child_cpu_ns', 'destroy_cpu_ns',
                   'destroy_wall_ns', 'rusage_self_maxrss_kib']
        for variant in binaries:
            group = [row['measurements'] for row in records if row['variant'] == variant]
            summary[variant] = {key: distribution([row[key] for row in group]) for key in scalars}
            summary[variant]['cpu_overrun_ns'] = distribution([v for row in group for v in row['cpu_overrun_ns']])
            for phase in ['before', 'steady', 'post_expiry', 'after']:
                summary[variant][phase] = {key: distribution([row[phase][key] for row in group]) for key in group[0][phase]}
        if any(digest(path) != value for path, value in initial.items()):
            raise ValueError('CPU helper benchmark input changed during measurement')
        report = dict(scope=__doc__, source_sha256=initial, inputs_unchanged=True, commands=commands,
            binaries=binaries, cpu_affinity=selected, sample_order='paired fresh processes; reverse pair order on odd sample',
            samples_per_variant=args.samples, rearms_per_process=1000, expiries_per_process=64,
            records=records, summary=summary,
            caveats=['Native CPU-engine comparison, not complete resource or guest/application acceptance.',
                     'Quantiles interpolate sorted observations; all individual values and extrema remain retained.',
                     'Two allowed CPUs shared by parent receiver and owned CPU-busy child.',
                     'CPU overrun measures child handler CPU timestamp minus absolute CPU deadline, not a wall-time deadline.',
                     'FD counts include the procfs observer itself; VmSize/VmData include libc arenas and stacks, not RSS.',
                     'RUSAGE_SELF maxrss can retain launcher/pre-exec high-water state; use procfs VmHWM for this executable, with raw values retained.',
                     'Previous SIGEV_THREAD helper remains alive for process lifetime; new service is explicitly joined/closed.',
                     'Original candidate and installed runtime files are unchanged.'])
        (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(dict(samples=len(records), output=str(output/'verification.json'))))
    except Exception:
        (output/'failed.json').write_text(json.dumps(dict(records=records, commands=commands, source_sha256=initial), indent=2)+'\n')
        raise


if __name__ == '__main__':
    main()
