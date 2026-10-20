#!/usr/bin/env python3
"""Matched native startup comparison: original, prepared, owned-storage lane."""
import argparse
import itertools
import json
from pathlib import Path
import statistics
import subprocess
import time

from benchmark_prepared_resource_startup import variant, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--previous', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--samples', type=int, default=24)
    args = parser.parse_args()
    if args.samples < 6:
        parser.error('at least six samples per variant are required')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tools = Path(__file__).resolve().parent
    source_paths = [Path(__file__).resolve(), tools / 'benchmark_prepared_resource_startup.py',
                    tools / 'patch_prepared_resource_lane_cost.py', tools / 'build_prepared_resource_lane_cost.py']
    source_hashes = {str(path): digest(path) for path in source_paths}
    variants = {
        'baseline': variant(args.baseline, output / 'baseline', False),
        'previous': variant(args.previous, output / 'previous', True),
        'owned_storage': variant(args.candidate, output / 'owned-storage', True),
    }
    permutations = list(itertools.permutations(variants))
    records = []
    for index in range(args.samples):
        for name in permutations[index % len(permutations)]:
            prefix = output / ('prefix-' + str(index) + '-' + name)
            prefix.mkdir()
            begin = time.monotonic_ns()
            result = subprocess.run([variants[name]['binary'], prefix.name], cwd=output,
                                    capture_output=True, text=True, timeout=30)
            elapsed = time.monotonic_ns() - begin
            (output / ('sample-' + str(index) + '-' + name + '.log')).write_text(result.stdout + result.stderr)
            result.check_returncode()
            line = next(line for line in result.stdout.splitlines() if line.startswith('DEARUST_STARTUP '))
            records.append(dict(index=index, variant=name, process_wall_ns=elapsed,
                                **json.loads(line.partition(' ')[2])))
    preserved = {name: expected for item in variants.values()
                 for name, expected in item['preserved_sha256'].items()}
    preserved.update(source_hashes)
    unchanged = all(digest(Path(name)) == expected for name, expected in preserved.items())
    keys = ('wall_ns', 'cpu_us', 'rss_kib', 'vmsize_kib', 'vmdata_kib', 'threads', 'process_wall_ns')
    summary = {name: {key: statistics.median(record[key] for record in records if record['variant'] == name)
                     for key in keys} for name in variants}
    report = dict(passed=unchanged, inputs_unchanged=unchanged, source_sha256=source_hashes,
                  medians=summary, samples=records, variants=variants,
                  scope='Fresh owned native startup only; all six variant orders rotated. '
                        'No RPC, guest workload, steady-state or broad performance acceptance claim. '
                        'Native pthread stack remains platform default; RSS residency varies; '
                        'raw ru_maxrss excluded because it may inherit pre-exec high water.')
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=unchanged, medians=summary), indent=2))
    if not unchanged:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
