#!/usr/bin/env python3
"""Validate the exact owned-server lazy CPU adapter without RPC or activation."""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import subprocess
from build_resource_cpu_dispatch_server import patch_cpu_dispatch


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('output must be absent')
    root = Path(__file__).resolve().parents[1]
    helper = root/'tools/resource_cpu_dispatch.inc'
    fixture = root/'tools/fixtures/resource-cpu-lazy-native.cpp'
    builder = root/'tools/build_resource_cpu_dispatch_server.py'
    inputs = [Path(__file__).resolve(), builder, helper, fixture,
              root/'work/resources-complete/server-build12/tools/resource-server-state.inc']
    initial = {str(path): digest(path) for path in inputs}
    output.mkdir(parents=True)
    text = patch_cpu_dispatch({'resource-cpu-timer.inc': '',
        'resource-server-state.inc': inputs[-1].read_text()}, int(signal.SIGRTMAX), helper.read_text())['resource-server-state.inc']
    begin = text.index('// Explicitly reserved native CPU-timer signal;')
    end = text.index('constexpr uint64_t resourceInfinity', begin)
    factory = text[begin:end]
    source = output/'resource-cpu-lazy-native.cpp'
    source.write_text(fixture.read_text().replace('@FACTORY@', factory))
    records = []
    for kind, flags in [('native', ['-O2']), ('asan-ubsan', ['-O1', '-g', '-fno-omit-frame-pointer', '-fsanitize=address,undefined'])]:
        binary = output/kind
        command = ['clang++', '-std=c++17', '-pthread', *flags,
            '-DDEARUST_CPU_HELPER=' + json.dumps(str(helper)), str(source), '-o', str(binary),
            '-Wl,--wrap=timer_create', '-Wl,--wrap=timer_settime', '-lrt']
        subprocess.run(command, check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        records.append(dict(kind=kind, command=command, binary_sha256=digest(binary),
            exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
        report = dict(scope=__doc__, source_sha256=initial, generated_source_sha256=digest(source),
            factory_sha256=hashlib.sha256(factory.encode()).hexdigest(), records=records,
            resource_ownership_activated=False, blocked_checks_retried=False,
            passed=all(row['exit_code']==0 for row in records),
            instrumentation='Native C++ lazy adapter and CPU helper are ASan/UBSan-instrumented in sanitizer variant; libc/kernel are not.')
        (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
        if result.returncode:
            raise ValueError('lazy CPU adapter regression failed')
    if any(digest(path) != value for path, value in initial.items()):
        raise ValueError('lazy CPU adapter inputs changed')
    report['inputs_unchanged'] = True
    (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(passed=True, variants=len(records))))


if __name__ == '__main__':
    main()
