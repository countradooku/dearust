#!/usr/bin/env python3
"""Compare whole wrapgen CLIs on an explicit library path; no speedup implied."""
import argparse
import json
import pathlib
import platform
import statistics
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=pathlib.Path)
    parser.add_argument('--rust', required=True, type=pathlib.Path)
    parser.add_argument('--library', required=True, type=pathlib.Path)
    parser.add_argument('--iterations', type=int, default=50)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error('at least two iterations required')
    library = args.library.resolve(strict=True)
    result = {
        'workload': 'whole wrapgen CLI, explicit library path, warm filesystem cache, source/header writes',
        'input': str(library), 'input_bytes': library.stat().st_size,
        'iterations': args.iterations, 'environment': platform.platform(),
        'upstream_commit': '60ba801decee7a00782f74f6be4c8ffb013f79ff',
        'upstream_build': 'c++ -O2 committed wrapgen.cpp -ldl',
        'rust_build': 'cargo build --release --offline --locked',
        'samples': {},
    }
    with tempfile.TemporaryDirectory(prefix='darling-wrapgen-bench-') as tmp:
        paths = [pathlib.Path(tmp) / name for name in ['source.c', 'variables.h']]
        expected = None
        # Alternate tool order to reduce systematic sequential-batch bias.
        samples = {'upstream': [], 'rust': []}
        executables = {'upstream': args.upstream.resolve(strict=True), 'rust': args.rust.resolve(strict=True)}
        for iteration in range(args.iterations + 5):
            order = ['upstream', 'rust'] if iteration % 2 == 0 else ['rust', 'upstream']
            for name in order:
                for path in paths:
                    path.unlink(missing_ok=True)
                start = time.perf_counter_ns()
                subprocess.run([str(executables[name]), str(library), *(str(p) for p in paths)], capture_output=True, check=True)
                elapsed = (time.perf_counter_ns() - start) / 1e6
                contents = tuple(path.read_bytes() if path.exists() else None for path in paths)
                if expected is None:
                    expected = contents
                if contents != expected:
                    raise RuntimeError('generated sources differ; benchmark is not comparable')
                if iteration >= 5:
                    samples[name].append(elapsed)
        for name, times in samples.items():
            result['samples'][name] = {
                'milliseconds': times, 'median_ms': statistics.median(times),
                'mean_ms': statistics.mean(times), 'stdev_ms': statistics.stdev(times),
                'min_ms': min(times), 'max_ms': max(times),
                'source_bytes': len(expected[0]),
            }
    result['outputs_identical'] = True
    result['limitations'] = 'Single host; warm cache; process startup and file writes included. Explicit path bypasses lookup. Not a Darling runtime benchmark; no general speedup established.'
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({name: {key: value for key, value in sample.items() if key != 'milliseconds'} for name, sample in result['samples'].items()}, indent=2))


if __name__ == '__main__':
    main()
