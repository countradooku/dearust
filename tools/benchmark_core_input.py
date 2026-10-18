#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Balanced same-output-inode core CLI benchmark, optionally across filesystems.

Uses the native fresh-process CPU/wall/RSS launcher and independent Mach-O decoder
from benchmark_core_states. Supply a valid owned fixture via --input. Optionally
copy it to a private temporary directory under --input-filesystem-root (/dev/shm
exercises the cross-filesystem bounded-copy fallback). Converter copies, raw
samples, hashes and summary stay under a fresh --output. No durability fsync is
included in timed conversion; one output inode is retained across all variants.
"""
import argparse
from contextlib import ExitStack
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import benchmark_core_states as core


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'input', 'original', 'prior', 'current'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--input-filesystem-root', type=Path)
    parser.add_argument('--rounds', type=int, default=36)
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    if args.rounds < 6 or args.rounds % 6:
        parser.error('rounds must be a positive multiple of six, at least six')
    output, source = args.output.resolve(), args.input.resolve(strict=True)
    binaries = {key: getattr(args, name).resolve(strict=True)
                for key, name in (('original_c', 'original'), ('prior_rust', 'prior'), ('current_rust', 'current'))}
    if source.is_relative_to(output) or any(path.is_relative_to(output) for path in binaries.values()):
        parser.error('output must be separate from supplied inputs')
    output.mkdir(parents=True, exist_ok=False)
    core.P = output
    for key, binary in binaries.items():
        core.BIN[key] = output / key
        shutil.copy2(binary, core.BIN[key])
    launcher = Path(__file__).resolve().parent / 'fixtures/core-benchmark-launcher.c'
    shutil.copy2(launcher, output / 'launcher.c')
    subprocess.run([args.cc, '-O2', '-Wall', '-Wextra', '-Werror', str(output / 'launcher.c'), '-o', str(output / 'launcher')], check=True)
    cpus = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpus[0]})
    report = dict(method=__doc__, source=str(source), source_sha256=core.sha(source),
                  source_bytes=source.stat().st_size, affinity_cpu=cpus[0],
                  script_sha256=core.sha(Path(__file__)),
                  binaries={key: dict(path=str(path), sha256=core.sha(path)) for key, path in core.BIN.items()},
                  started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    with ExitStack() as stack:
        if args.input_filesystem_root:
            temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='dearust-core-bench-', dir=args.input_filesystem_root)))
            copied = temporary / 'input.elf'
            shutil.copyfile(source, copied)
            with copied.open('rb') as stream:
                os.fsync(stream.fileno())
            source = copied
            assert core.sha(source) == report['source_sha256']
        report['input_device'] = source.stat().st_dev
        report['output_device'] = output.stat().st_dev
        report['cross_filesystem'] = report['input_device'] != report['output_device']
        destination = output / 'shared-output.macho'
        destination.touch()
        inode = destination.stat().st_ino
        checks, first = {}, {}
        for key, binary in core.BIN.items():
            first[key] = core.run(binary, source, destination)
            checks[key] = core.parse_output(destination)
        for key in checks:
            for field in ('gpr', 'segments'):
                assert checks[key][field] == checks['original_c'][field], (key, field)
        for field in ('gpr', 'fp', 'raw', 'segments'):
            assert checks['current_rust'][field] == checks['prior_rust'][field], field
        for _ in range(2):
            for binary in core.BIN.values():
                core.run(binary, source, destination)
        samples = []
        orders = list(itertools.permutations(core.BIN))
        for round_number in range(args.rounds):
            for position, key in enumerate(orders[round_number % len(orders)]):
                sample = core.run(core.BIN[key], source, destination)
                assert destination.stat().st_ino == inode
                samples.append(dict(round=round_number, position=position, variant=key, **sample))
        report.update(destination_inode=inode, first_process=first, validation=checks, samples=samples,
                      summary={key: {metric: core.distribution([sample[metric] for sample in samples if sample['variant'] == key])
                                     for metric in ('cpu_us', 'wall_us', 'maxrss_kib', 'minor_faults', 'major_faults', 'output_bytes')}
                               for key in core.BIN})
    assert core.sha(args.input.resolve(strict=True)) == report['source_sha256']
    report['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    (output / 'benchmark-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: {metric: row[metric]['median'] for metric in ('cpu_us', 'wall_us', 'maxrss_kib', 'output_bytes')}
                      for key, row in report['summary'].items()}, indent=2))


if __name__ == '__main__':
    main()
