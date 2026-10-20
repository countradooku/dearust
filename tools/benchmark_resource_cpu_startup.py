#!/usr/bin/env python3
"""Measure previous/new owned native startup at the SAME cleanup-ready boundary.

No resource calls, RPC, cleanup-lane redesign or guest fault injection occurs.
The existing initialized cleanup lane is identical in both variants.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time
from benchmark_prepared_resource_startup import variant


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=20)
    args = parser.parse_args()
    if args.samples < 3:
        parser.error('at least three fresh processes per variant required')
    output = args.output.resolve()
    if output.exists():
        parser.error('output must be absent')
    sources = [Path(__file__).resolve(), Path(__file__).with_name('benchmark_prepared_resource_startup.py')]
    hashes = {str(path): digest(path) for path in sources}
    output.mkdir(parents=True)
    # Both have the existing cleanup lane, so both must use prepared=True.
    variants = {name: variant(path, output/name, True)
                for name, path in [('previous', args.previous), ('current', args.current)]}
    records = []
    for index in range(args.samples):
        order = ('previous', 'current') if index % 2 == 0 else ('current', 'previous')
        for name in order:
            prefix = output/('prefix-'+str(index)+'-'+name)
            prefix.mkdir()
            command = [variants[name]['binary'], prefix.name]
            begin = time.monotonic_ns()
            run = subprocess.run(command, cwd=output, capture_output=True, text=True, timeout=30)
            elapsed = time.monotonic_ns()-begin
            record = dict(variant=name, sample=index, command=command, exit_code=run.returncode,
                          stdout=run.stdout, stderr=run.stderr, process_wall_ns=elapsed)
            records.append(record)
            (output/'raw.json').write_text(json.dumps(records, indent=2)+'\n')
            if run.returncode:
                raise ValueError('owned native startup measurement failed: ' + name)
            line = next(line for line in run.stdout.splitlines() if line.startswith('DEARUST_STARTUP '))
            record['measurements'] = json.loads(line.partition(' ')[2])
    for name, value in hashes.items():
        if digest(name) != value:
            raise ValueError('startup tool changed during measurement')
    for data in variants.values():
        for name, value in data['preserved_sha256'].items():
            if digest(name) != value:
                raise ValueError('startup candidate changed: ' + name)
    summary = {}
    keys = ['wall_ns', 'cpu_us', 'rss_kib', 'vmsize_kib', 'vmdata_kib', 'threads']
    for name in variants:
        group = [record['measurements'] for record in records if record['variant']==name]
        summary[name] = {key:dict(median=statistics.median([row[key] for row in group]),
            minimum=min(row[key] for row in group), maximum=max(row[key] for row in group)) for key in keys}
        summary[name]['process_wall_ns'] = dict(median=statistics.median(record['process_wall_ns'] for record in records if record['variant']==name))
    report = dict(scope=__doc__, passed=True, inputs_unchanged=True, tool_sha256=hashes,
        variants=variants, records=records, summary=summary,
        resource_ownership_activated=False, blocked_checks_retried=False,
        caveats=['Native initialization stops at identical cleanup-ready boundary; no RPC or active CPU-limit workload.',
                 'ResourceLazyCpuTimer separately proves no receiver/FDs before first arm.',
                 'Raw ru_maxrss may retain pre-exec high water; excluded from summaries. RSS/VM values include libraries and allocator residency.',
                 'No whole-resource performance or shutdown/fault acceptance claim.'])
    (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(passed=True, summary=summary), indent=2))


if __name__ == '__main__':
    main()
