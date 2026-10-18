#!/usr/bin/env python3
"""Summarize unchanged guest workloads in original/Rust/Rust/original order.

CPU uses 21 batched samples per process. Optional resource reports use repeated
512-round processes: medians of per-process quantiles are not pooled quantiles.
The original label in the reverse input denotes Rust. This tool validates both
kernel mappings, shutdown and strict correctness results before accepting data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics


def distribution(values):
    quartiles = statistics.quantiles(values, n=4) if len(values) > 1 else [values[0]] * 3
    return dict(samples=len(values), minimum=min(values), q1=quartiles[0],
                median=statistics.median(values), q3=quartiles[2], maximum=max(values))


def kernel_pair(forward, reverse):
    inputs = [json.loads(path.read_text()) for path in (forward, reverse)]
    variants = []
    for data in inputs:
        if len(data['variants']) != 2 or {v['variant'] for v in data['variants']} != {'original', 'rust'}:
            raise ValueError('expected exactly two kernel variants')
        variants.append({v['variant']: v for v in data['variants']})
    for first, second in [('original', 'rust'), ('rust', 'original')]:
        if variants[0][first]['kernel_sha256'] != variants[1][second]['kernel_sha256']:
            raise ValueError('reverse experiment does not use the same reversed kernels')
    reference = {r['name']: (r['executable_sha256'], r['expected_stdout_sha256'])
                 for r in inputs[0]['variants'][0]['results']}
    for data in inputs:
        for variant in data['variants']:
            actual = {r['name']: (r['executable_sha256'], r['expected_stdout_sha256'])
                      for r in variant['results']}
            if actual != reference or len(actual) != len(variant['results']):
                raise ValueError('workload binaries/goldens differ or cases are duplicated')
    return (variants[0]['original']['kernel_sha256'], variants[0]['rust']['kernel_sha256'])


def reports(forward, reverse):
    kernel_pair(forward, reverse)
    for order, path in [('forward', forward), ('reverse', reverse)]:
        data = json.loads(path.read_text())
        if {v['variant'] for v in data['variants']} != {'original', 'rust'}:
            raise ValueError('expected both original and Rust variants')
        for variant in data['variants']:
            if (variant['shutdown']['exit_code'] != 0 or variant['shutdown']['timed_out']
                    or not variant['kernel_mappings']):
                raise ValueError('missing runtime mapping or clean shutdown: ' + str(path))
            name = ('baseline' if variant['variant'] == 'original' else 'rust')
            if order == 'reverse':
                name = 'rust' if name == 'baseline' else 'baseline'
            for result in variant['results']:
                if not result['strict_pass']:
                    raise ValueError('correctness failure: ' + result['name'])
                yield order, name, result


def cpu_summary(forward, reverse):
    families = {}
    for order, variant, result in reports(forward, reverse):
        family = result['name'].split('-')[0]
        if family not in ('metadata', 'permissions'):
            raise ValueError('unexpected workload: ' + result['name'])
        pattern = (family.upper() + r'_BENCH sample=(\d+) roundtrips=(\d+) '
                   r'cpu_us=(\d+) wall_us=(\d+)')
        samples = re.findall(pattern, result['stderr'])
        if len(samples) != 21 or {int(row[0]) for row in samples} != set(range(21)):
            raise ValueError('expected exactly 21 complete samples')
        records = families.setdefault(family, [])
        for sample, rounds, cpu, wall in samples:
            expected = 2000 if family == 'metadata' else 200
            if int(rounds) != expected:
                raise ValueError('unexpected workload iteration count')
            records.append(dict(order=order, variant=variant, sample=int(sample),
                                roundtrips=int(rounds), cpu_us=int(cpu), wall_us=int(wall)))
    output = {}
    for family, records in families.items():
        summary = {}
        for variant in ('baseline', 'rust'):
            selected = [r for r in records if r['variant'] == variant]
            if len(selected) != 42:
                raise ValueError('expected 42 samples per variant')
            summary[variant] = dict(cpu_us_per_roundtrip=distribution([
                r['cpu_us'] / r['roundtrips'] for r in selected]),
                wall_us_per_roundtrip=distribution([
                    r['wall_us'] / r['roundtrips'] for r in selected]))
        summary['cpu_change_percent'] = 100 * (
            summary['rust']['cpu_us_per_roundtrip']['median'] /
            summary['baseline']['cpu_us_per_roundtrip']['median'] - 1)
        output[family] = dict(summary=summary, records=records)
    if output.keys() != {'metadata', 'permissions'}:
        raise ValueError('both workloads are required')
    return output


def resource_summary(forward, reverse):
    families = {}
    required = {'rounds', 'cpu_us', 'first_round_us', 'p50_us', 'p95_us', 'p99_us',
                'max_us', 'maxrss_linux_kib', 'initial_maxrss_linux_kib',
                'rss_linux_kib', 'pss_linux_kib', 'private_dirty_linux_kib'}
    for order, variant, result in reports(forward, reverse):
        family = result['name'].split('-')[0]
        if family not in ('metadata', 'permissions'):
            raise ValueError('unexpected resource workload')
        lines = [line for line in result['stderr'].splitlines()
                 if line.startswith(family.upper() + '_RESOURCE ')]
        if len(lines) != 1:
            raise ValueError('missing unique resource sample')
        values = {name: int(value) for name, value in re.findall(r'(\w+)=(\d+)', lines[0])}
        if values.keys() != required or values['rounds'] != 512:
            raise ValueError('unexpected resource fields or workload length')
        values['maxrss_growth_linux_kib'] = values['maxrss_linux_kib'] - values['initial_maxrss_linux_kib']
        families.setdefault(family, []).append(dict(order=order, variant=variant,
                                                   case=result['name'], values=values))
    output = {}
    for family, records in families.items():
        summary = {}
        for variant in ('baseline', 'rust'):
            selected = [r['values'] for r in records if r['variant'] == variant]
            if not selected:
                raise ValueError('missing resource variant')
            summary[variant] = {field: distribution([row[field] for row in selected])
                                for field in selected[0]}
        if summary['baseline']['rounds']['samples'] != summary['rust']['rounds']['samples']:
            raise ValueError('resource variants have different sample counts')
        output[family] = dict(summary=summary, records=records)
    if output.keys() != {'metadata', 'permissions'}:
        raise ValueError('both resource workloads are required')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--forward', type=Path, required=True)
    parser.add_argument('--reverse', type=Path, required=True)
    parser.add_argument('--resource-forward', type=Path)
    parser.add_argument('--resource-reverse', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if bool(args.resource_forward) != bool(args.resource_reverse):
        parser.error('both resource reports are required')
    inputs = [args.forward, args.reverse]
    result = dict(scope=__doc__, cpu=cpu_summary(args.forward, args.reverse))
    if args.resource_forward:
        if kernel_pair(args.forward, args.reverse) != kernel_pair(args.resource_forward, args.resource_reverse):
            raise ValueError('CPU and resource experiments use different kernels')
        result['resources'] = resource_summary(args.resource_forward, args.resource_reverse)
        inputs += [args.resource_forward, args.resource_reverse]
    result['inputs'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({family: data['summary']['cpu_change_percent']
                      for family, data in result['cpu'].items()}, indent=2))


if __name__ == '__main__':
    main()
