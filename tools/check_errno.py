#!/usr/bin/env python3
"""Verify the native errno object against committed pinned C and real headers.

Requires clang (including UBSan), nm, readelf, and local exact-pin Git objects.
No worktree source is read and no SDK constants or runtime stubs are invented.
The optional C microbenchmark measures conversion calls only, not Darling speed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile

DARLING_PIN = '60ba801decee7a00782f74f6be4c8ffb013f79ff'
XNU_PIN = 'fa29287aa2f0115271e091f1031f53c9e024005d'
EMULATION = 'darling/src/libsystem_kernel/emulation'
SDK = 'Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/usr/include/sys'
SYMBOLS = {'errno_linux_to_bsd', 'length_of_translation_array'}


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True,
                          text=True, timeout=60, **kwargs)


def extract(repo, pin, source, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    blob = subprocess.check_output(['git', '-C', str(repo), 'show', f'{pin}:{source}'])
    output.write_bytes(blob)
    return hashlib.sha256(blob).hexdigest()


HARNESS = r'''
#define _POSIX_C_SOURCE 200809L
#include <darling_errno.h>
#include <assert.h>
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
extern int upstream_errno_linux_to_bsd(int);
extern const size_t upstream_length_of_translation_array;
static volatile uint64_t sink;
static uint64_t now(void) {
    struct timespec value;
    assert(clock_gettime(CLOCK_MONOTONIC, &value) == 0);
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) + value.tv_nsec;
}
static uint64_t bench(int (*convert)(int)) {
    uint64_t start = now(), total = 0;
    uint32_t bits = 42;
    for (unsigned i = 0; i < 1000000; ++i) {
        bits = bits * UINT32_C(1664525) + UINT32_C(1013904223);
        // Mixture of real errors, signs, table holes and out-of-range inputs.
        int input = (int)(bits % 1201) - 600;
        total += (uint32_t)convert(input);
    }
    uint64_t elapsed = now() - start;
    sink = total;
    return elapsed;
}
int main(int argc, char **argv) {
    uint64_t hash = UINT64_C(14695981039346656037);
    unsigned random = 0;
    assert(length_of_translation_array == 512);
    assert(upstream_length_of_translation_array == length_of_translation_array);
    for (int input = -65536; input <= 65536; ++input) {
        int result = errno_linux_to_bsd(input);
        assert(result == upstream_errno_linux_to_bsd(input));
        hash = (hash ^ (uint32_t)result) * UINT64_C(1099511628211);
    }
    uint32_t bits = 42;
    for (unsigned i = 0; i < 1000000; ++i) {
        bits = bits * UINT32_C(1664525) + UINT32_C(1013904223);
        int input = bits <= INT_MAX ? (int)bits : -1 - (int)(UINT32_MAX - bits);
        if (input == INT_MIN) continue; // C's signed-negation UB is checked separately.
        int result = errno_linux_to_bsd(input);
        assert(result == upstream_errno_linux_to_bsd(input));
        hash = (hash ^ (uint32_t)result) * UINT64_C(1099511628211);
        ++random;
    }
    assert(errno_linux_to_bsd(INT_MIN) == INT_MIN);
    assert(errno_linux_to_bsd(INT_MAX) == upstream_errno_linux_to_bsd(INT_MAX));
    assert(errno_linux_to_bsd(INT_MIN + 1) == upstream_errno_linux_to_bsd(INT_MIN + 1));
    printf("range=131073 random=%u length=%zu min=%d hash=%016" PRIx64 "\n",
           random, length_of_translation_array, errno_linux_to_bsd(INT_MIN), hash);
    if (argc > 1 && strcmp(argv[1], "bench") == 0) {
        for (int sample = -2; sample < 21; ++sample) {
            uint64_t upstream, rust;
            if (sample % 2 == 0) {
                upstream = bench(upstream_errno_linux_to_bsd);
                uint64_t expected = sink;
                rust = bench(errno_linux_to_bsd);
                assert(sink == expected);
            } else {
                rust = bench(errno_linux_to_bsd);
                uint64_t expected = sink;
                upstream = bench(upstream_errno_linux_to_bsd);
                assert(sink == expected);
            }
            if (sample >= 0) printf("sample=%d upstream_ns=%" PRIu64 " rust_ns=%" PRIu64 "\n", sample, upstream, rust);
        }
    }
    return 0;
}
'''


def verify(args):
    rust_object = args.object.resolve(strict=True)
    defined = run(['nm', '-g', '--defined-only', '--format=posix', rust_object]).stdout
    if {line.split()[0] for line in defined.splitlines()} != SYMBOLS:
        raise ValueError('native object must define exactly the two original C globals')
    if run(['nm', '-u', rust_object]).stdout.strip():
        raise ValueError('errno object unexpectedly requires external runtime symbols')
    symbols = run(['readelf', '-sW', rust_object]).stdout
    for name in SYMBOLS:
        if not any('GLOBAL HIDDEN' in line and line.endswith(name) for line in symbols.splitlines()):
            raise ValueError('original global hidden visibility missing: ' + name)
    with tempfile.TemporaryDirectory(prefix='darling-errno-') as directory:
        work = Path(directory)
        inputs = {}
        for filename in ['errno.h', 'cdefs.h', '_types/_errno_t.h']:
            source = 'bsd/sys/' + filename
            inputs['xnu:' + source] = extract(args.xnu, XNU_PIN, source, work/'sys'/filename)
        for filename in ['conversion/errno.h', 'conversion/duct_errno.h', 'common/base.h']:
            source = EMULATION + '/include/' + filename
            inputs['xnu:' + source] = extract(args.xnu, XNU_PIN, source, work/'darling/emulation'/filename)
        source = EMULATION + '/src/conversion/errno.c'
        inputs['xnu:' + source] = extract(args.xnu, XNU_PIN, source, work/'errno.c')
        for filename in ['_symbol_aliasing.h', '_posix_availability.h']:
            source = SDK + '/' + filename
            inputs['darling:' + source] = extract(args.upstream, DARLING_PIN, source, work/'sys'/filename)
        flags = ['clang', '-O2', '-fPIC', '-fvisibility=hidden', '-I', work,
                 '-include', work/'sys/errno.h']
        macros = run([*flags, '-dM', '-E', work/'errno.c']).stdout
        for macro in ['#define __DARWIN_UNIX03 1', '#define EOPNOTSUPP 102', '#define ENOTSUP 45']:
            if macro not in macros.splitlines():
                raise ValueError('reference Darwin ABI macro mismatch: ' + macro)
        run([*flags, '-Derrno_linux_to_bsd=upstream_errno_linux_to_bsd',
             '-Dlength_of_translation_array=upstream_length_of_translation_array',
             '-c', work/'errno.c', '-o', work/'original.o'])
        (work/'harness.c').write_text(HARNESS)
        include = Path(__file__).resolve().parent.parent/'crates/darling-errno/include'
        run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-I', include,
             work/'harness.c', work/'original.o', rust_object, '-o', work/'compare'])
        command = [work/'compare'] + (['bench'] if args.benchmark else [])
        output = run(command).stdout.splitlines()
        # Reproduce the defect on the original object under UBSan, never run its
        # undefined INT_MIN path as a normal differential test.
        (work/'minimum.c').write_text("""#include <limits.h>
int errno_linux_to_bsd(int);
int main(void) { volatile int input=INT_MIN; return errno_linux_to_bsd(input)==input ? 0 : 2; }
""")
        run([*flags, '-fsanitize=undefined', '-fno-sanitize-recover=undefined',
             '-c', work/'errno.c', '-o', work/'ubsan.o'])
        run(['clang', '-fsanitize=undefined', work/'minimum.c', work/'ubsan.o', '-o', work/'ubsan'])
        defect = subprocess.run([str(work/'ubsan')], capture_output=True, text=True, timeout=10)
        if defect.returncode == 0 or 'negation of -2147483648' not in defect.stderr:
            raise ValueError('expected original INT_MIN overflow was not reproduced: ' + defect.stderr)
        record = {
            'scope': 'Native Linux C ABI errno object only; no Darwin runtime or whole emulation target verification',
            'darling_pin': DARLING_PIN, 'xnu_pin': XNU_PIN,
            'input_sha256': inputs, 'differential': output[0],
            'original_int_min_ubsan': 'Confirmed signed negation overflow; fail-fast UBSan exit ' + str(defect.returncode),
            'rust_int_min': -2147483648,
            'object': {'defined_globals': sorted(SYMBOLS), 'undefined_symbols': [], 'visibility': 'GLOBAL HIDDEN', 'size_bytes': rust_object.stat().st_size, 'sha256': hashlib.sha256(rust_object.read_bytes()).hexdigest()},
            'compiler': run(['clang', '--version']).stdout.splitlines()[0],
            'rustc': run(['rustc', '--version']).stdout.strip(),
            'rust_source_sha256': {name: hashlib.sha256((include.parent/'src'/name).read_bytes()).hexdigest() for name in ['lib.rs', 'abi.rs', 'conversion.rs']},
            'platform': platform.platform(),
            'cpu': next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), platform.machine()),
        }
        if args.benchmark:
            samples = [{key: int(value) for key,value in (field.split('=') for field in line.split())} for line in output[1:]]
            record['benchmark'] = {
                'workload': '1000000 out-of-line conversion calls per sample, deterministic mixed signed errors/holes, 2 warmups and 21 alternating samples',
                'samples': samples,
                'upstream_median_ns_per_call': statistics.median(s['upstream_ns'] for s in samples)/1000000,
                'rust_median_ns_per_call': statistics.median(s['rust_ns'] for s in samples)/1000000,
                'checksums_identical': True,
                'limitations': 'Single local synthetic conversion workload, includes call overhead; not syscall latency, macOS app performance or general Darling speedup',
            }
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--xnu', type=Path, required=True)
    parser.add_argument('--object', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--benchmark', action='store_true')
    args = parser.parse_args()
    record = verify(args)
    if args.report:
        args.report.write_text(json.dumps(record, indent=2) + '\n')
    print(record['differential'])
    print(record['original_int_min_ubsan'])
    if args.benchmark:
        print(json.dumps({key: value for key,value in record['benchmark'].items() if key != 'samples'}))


if __name__ == '__main__':
    main()
