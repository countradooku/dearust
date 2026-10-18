#!/usr/bin/env python3
"""Summarize real guest region samples from old/new then new/old comparisons."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics

LINE = re.compile(r'^REGION_BENCH flavor=(8|22) sample=(\d+) calls=(\d+) cpu_us=(\d+) wall_us=(\d+)$', re.M)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('forward', 'reverse', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    records = []
    for order in ('forward', 'reverse'):
        data = json.loads(getattr(args, order).read_text())
        for entry in data['variants']:
            result = next(r for r in entry['results'] if r['name'] == 'region-query-bench.c')
            if not result['strict_pass'] or not entry['kernel_mappings']:
                raise ValueError('benchmark correctness or mapping evidence failed')
            variant = ('baseline' if entry['variant'] == 'original' else 'optimized') if order == 'forward' else ('optimized' if entry['variant'] == 'original' else 'baseline')
            matches = LINE.findall(result['stderr'])
            if len(matches) != 42 or {(int(f), int(s)) for f,s,_,_,_ in matches} != {(f,s) for f in (8,22) for s in range(21)}:
                raise ValueError('expected 21 independent samples of each query flavor')
            for flavor, sample, calls, cpu, wall in matches:
                if int(calls) <= 0:
                    raise ValueError('invalid call count')
                records.append(dict(order=order, variant=variant, flavor=int(flavor), sample=int(sample), calls=int(calls), cpu_us=int(cpu), wall_us=int(wall)))
    summary = []
    for flavor in (8,22):
        row = {'flavor': flavor}
        for variant in ('baseline','optimized'):
            data = [r for r in records if r['flavor'] == flavor and r['variant'] == variant]
            cpu = [r['cpu_us']/r['calls'] for r in data]
            wall = [r['wall_us']/r['calls'] for r in data]
            q = statistics.quantiles(cpu, n=4, method='inclusive')
            row[variant] = {'samples': len(data), 'median_cpu_us_per_call': statistics.median(cpu),
                            'cpu_q1_us_per_call': q[0], 'cpu_q3_us_per_call': q[2],
                            'median_wall_us_per_call': statistics.median(wall)}
        row['cpu_change_percent'] = 100*(row['optimized']['median_cpu_us_per_call']/row['baseline']['median_cpu_us_per_call']-1)
        summary.append(row)
    report = {'scope': 'Actual matching guest runtime, 128 aliases of a file, querying highest address. Four process runs in old/new/new/old order. CPU via guest getrusage; wall via gettimeofday. Workload-specific, not whole-app performance.',
              'inputs': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.forward,args.reverse)},
              'records': records, 'summary': summary}
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
