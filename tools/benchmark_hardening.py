#!/usr/bin/env python3
"""Compare previous/new grouped Rust objects with identical native adapters.

This measures scoped wrapper workloads with native credential adapters and real
Darwin stat layouts, excluding real guest TLS/virtual-root expansion. Object paths are explicit; no installed runtime
is modified. Each benchmark validates its owned files and guards before exiting.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=21)
    parser.add_argument('--iterations', type=int, default=2000)
    args = parser.parse_args()
    if args.samples < 3 or args.iterations < 1:
        parser.error('at least three samples and positive iterations required')
    args.output.mkdir(parents=True, exist_ok=False)
    fixtures = Path(__file__).resolve().parent / 'fixtures'
    stages = {'baseline': args.baseline.resolve(), 'rust': args.runtime.resolve()}
    commands, reports = [], {}
    cpu = min(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    for family in ('metadata', 'permissions'):
        driver = fixtures / (family + '-bench.c')
        binaries = {}
        for variant, stage in stages.items():
            binary = args.output / (family + '-' + variant)
            command = ['clang', '-O2', '-Wall', '-Wextra', '-Werror', str(driver),
                       str(fixtures / 'path-unused-boundaries-native.c'),
                       str(stage / 'objects/paths.o'), '-o', str(binary)]
            imports = subprocess.check_output(['nm', '-u', str(stage / 'objects/paths.o')], text=True)
            if 'dearust_copy_signals_ready' in imports:
                command.insert(-2, str(fixtures / 'copy-signal-native.c'))
            subprocess.run(command, check=True)
            commands.append(command)
            binaries[variant] = binary
        records = []
        for sample in range(args.samples):
            order = ('baseline', 'rust') if sample % 2 == 0 else ('rust', 'baseline')
            for variant in order:
                result = subprocess.check_output([str(binaries[variant]), str(args.iterations)], text=True)
                rounds, cpu_ns, wall_ns = map(int, result.split())
                assert rounds == args.iterations
                records.append(dict(sample=sample, variant=variant, roundtrips=rounds,
                                    cpu_ns=cpu_ns, wall_ns=wall_ns))
        summary = {}
        for variant in stages:
            rows = [x for x in records if x['variant'] == variant]
            cpu_values = [x['cpu_ns'] / x['roundtrips'] for x in rows]
            quartiles = statistics.quantiles(cpu_values, n=4)
            summary[variant] = dict(median_cpu_ns_per_roundtrip=statistics.median(cpu_values),
                                   cpu_q1_ns_per_roundtrip=quartiles[0],
                                   cpu_q3_ns_per_roundtrip=quartiles[2],
                                   median_wall_ns_per_roundtrip=statistics.median(
                                       x['wall_ns'] / x['roundtrips'] for x in rows))
        summary['cpu_change_percent'] = (summary['rust']['median_cpu_ns_per_roundtrip'] /
                                         summary['baseline']['median_cpu_ns_per_roundtrip'] - 1) * 100
        reports[family] = dict(driver_sha256=digest(driver), records=records, summary=summary)
    report = dict(scope=__doc__, cpu=cpu, samples=args.samples, iterations=args.iterations,
                  commands=commands, objects={v: dict(path=str(p / 'objects/paths.o'),
                      sha256=digest(p / 'objects/paths.o')) for v, p in stages.items()},
                  families=reports)
    (args.output / 'benchmark.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v['summary'] for k, v in reports.items()}, indent=2))


if __name__ == '__main__':
    main()
