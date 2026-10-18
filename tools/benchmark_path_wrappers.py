#!/usr/bin/env python3
"""Measure old/new wrapper objects, with explicit synthetic versus raw-Linux scope."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'optimized', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--samples', type=int, default=21)
    parser.add_argument('--iterations', type=int, default=2000)
    args = parser.parse_args()
    if args.samples < 3 or args.iterations < 1:
        parser.error('at least three samples and positive iterations required')
    args.output.mkdir(parents=True, exist_ok=False)
    # Restrict only this benchmark process and its children, never the user session.
    cpu = min(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    driver = Path(__file__).parent/'fixtures/path-wrapper-bench.c'
    binaries = {}
    for variant in ('baseline', 'optimized'):
        stage = getattr(args, variant).resolve(strict=True)
        binary = args.output/variant
        subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', str(driver),
                        *[str(stage/'objects'/(name+'.o')) for name in ('lstat', 'fstatat', 'symlinkat')],
                        '-o', str(binary)], check=True)
        binaries[variant] = binary
    records = []
    for sample in range(args.samples):
        for variant in (('baseline', 'optimized') if sample % 2 == 0 else ('optimized', 'baseline')):
            raw = subprocess.check_output([str(binaries[variant]), str(args.iterations)], text=True)
            for line in raw.splitlines():
                name, length, calls, cpu_ns, wall_ns = line.split()
                records.append(dict(sample=sample, variant=variant, workload=name, length=int(length),
                                    calls=int(calls), cpu_ns=int(cpu_ns), wall_ns=int(wall_ns)))
    summary = []
    for name, length in sorted({(r['workload'], r['length']) for r in records}):
        row = {'workload': name, 'path_bytes': length}
        for variant in binaries:
            matching = [r for r in records if r['variant'] == variant and r['workload'] == name and r['length'] == length]
            timings = [r['cpu_ns']/r['calls'] for r in matching]
            quartiles = statistics.quantiles(timings, n=4, method='inclusive')
            row[variant] = {'cpu_q1_ns_per_call': quartiles[0], 'cpu_q3_ns_per_call': quartiles[2],
                            'median_cpu_ns_per_call': statistics.median(timings),
                            'median_wall_ns_per_call': statistics.median(r['wall_ns']/r['calls'] for r in matching)}
        row['cpu_change_percent'] = 100*(row['optimized']['median_cpu_ns_per_call']/row['baseline']['median_cpu_ns_per_call']-1)
        summary.append(row)
    report = {'scope': 'stub-* excludes OS/vchroot/conversion costs; linux-fstatat uses real Linux syscall with adapted guest boundaries. No full runtime speedup claim.',
              'inputs': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [driver, *[getattr(args, v)/'objects'/(n+'.o') for v in binaries for n in ('lstat','fstatat','symlinkat')]]},
              'timers': 'CLOCK_THREAD_CPUTIME_ID and CLOCK_MONOTONIC',
              'cpu': cpu, 'samples': args.samples, 'iterations': args.iterations, 'records': records, 'summary': summary}
    (args.output/'benchmark.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
