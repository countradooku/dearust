#!/usr/bin/env python3
"""Compare exact pinned filesystem C algorithms and Rust using native boundaries.

Four calls per round: statfs, statfs64, fstatfs, fstatfs64. Native identity TLS/
virtual-root and Linux open/read/close/fdpath adapters are explicit; the legacy
mount reader and converter bodies are exact pinned source extracts. Rust uses
its guarded copy bridge. Actual guest measurements retain all original helpers
and remain the authoritative runtime comparison; these are native microbenchmarks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

from benchmark_fileops import pinned_hashes
from check_errno import XNU_PIN


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(text, start, end):
    first = text.index(start)
    last = text.index(end, first)
    body = text[first:last]
    return body, dict(start_byte=len(text[:first].encode()), end_byte_exclusive=len(text[:last].encode()),
                      first_line=text.count('\n', 0, first)+1,
                      last_line=text.count('\n', 0, last),
                      sha256=hashlib.sha256(body.encode()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'runtime', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--samples', type=int, default=21)
    parser.add_argument('--iterations', type=int, default=1000)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--build-only', action='store_true')
    mode.add_argument('--run-only', action='store_true')
    args = parser.parse_args()
    if args.samples < 3 or args.iterations < 1:
        parser.error('require at least three samples and positive iterations')
    source, runtime = args.source.resolve(strict=True), args.runtime.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        parser.error('output must be separate from reference source')
    base = source/'src/external/xnu/darling/src/libsystem_kernel/emulation'
    fixture_dir = Path(__file__).resolve().parent/'fixtures'
    fixture = fixture_dir/'filesystem-bench.c'
    if args.run_only:
        report = json.loads((output/'benchmark-build.json').read_text())
        for key in ('source_sha256', 'fixture_sha256', 'rust_objects_sha256', 'generated_sha256', 'binaries_sha256'):
            for name, expected in report[key].items():
                if digest(Path(name)) != expected:
                    raise ValueError('benchmark input changed after build: '+name)
        if report['source_root'] != str(source) or report['runtime_root'] != str(runtime):
            parser.error('source/runtime paths differ from recorded build')
    else:
        output.mkdir(parents=True, exist_ok=False)
        headers = output/'headers'
        commands = []
        sources = [base/'src/xnu_syscall/bsd/impl/stat'/f'{name}.c' for name in ('statfs', 'fstatfs')]
        helper_sources = [base/'src/common/simple.c', base/'src/conversion/stat/common.c']
        header_names = ('conversion/stat/common.h', 'common/simple.h',
                        'xnu_syscall/bsd/impl/stat/statfs.h', 'xnu_syscall/bsd/impl/stat/fstatfs.h')
        hashes = pinned_hashes(source, sources+helper_sources+[base/'include'/name for name in header_names])
        def header(name, text):
            target = headers/'darling/emulation'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        for path in sources:
            for line in path.read_text().splitlines():
                if line.startswith('#include <darling/'):
                    name = line.split('<', 1)[1].split('>', 1)[0]
                    target = headers/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text('/* Explicit native boundary header. */\n')
        for name in header_names:
            header(name, (base/'include'/name).read_text())
        header('common/base.h', '''#include <sys/syscall.h>
#include <string.h>
#define CPP_EXTERN_BEGIN
#define CPP_EXTERN_END
long linux_syscall(long,long,long,long,long,long,int);
#define LINUX_SYSCALL2(n,a,b) linux_syscall((long)(a),(long)(b),0,0,0,0,n)
#define LINUX_SYSCALL3(n,a,b,c) linux_syscall((long)(a),(long)(b),(long)(c),0,0,0,n)
''')
        header('conversion/errno.h', 'int errno_linux_to_bsd(int);\n')
        header('common/bsdthread/per_thread_wd.h', 'int dearust_get_perthread_wd(void);\n#define get_perthread_wd dearust_get_perthread_wd\n')
        header('linux_premigration/vchroot_expand.h', 'struct vchroot_expand_args {char path[4096];unsigned flags;int dfd;};\nint vchroot_expand(struct vchroot_expand_args *);\n')
        header('xnu_syscall/bsd/impl/fcntl/open.h', 'long sys_open(const char *,int,int);\n')
        header('xnu_syscall/bsd/impl/unistd/close.h', 'long close_internal(int);\n')
        header('xnu_syscall/bsd/helper/misc/fdpath.h', '#include <stddef.h>\nint fdpath(int,char *,size_t);\n')
        reader, reader_range = extract(helper_sources[0].read_text(),
            'extern void *memcpy(void *dest, const void *src, __SIZE_TYPE__ n);',
            '__attribute__ ((visibility ("default")))\nvoid __simple_abort')
        converter_text = helper_sources[1].read_text()
        converter_start = converter_text.index('void statfs_linux_to_bsd64(')
        converter_end = converter_text.index('\n}', converter_start)+2
        converter = converter_text[converter_start:converter_end]
        converter_range = dict(start_byte=len(converter_text[:converter_start].encode()), end_byte_exclusive=len(converter_text[:converter_end].encode()),
            first_line=converter_text.count('\n', 0, converter_start)+1,
            last_line=converter_text.count('\n', 0, converter_end)+1,
            sha256=hashlib.sha256(converter.encode()).hexdigest())
        extracted_reader, extracted_converter = output/'original-readline.c', output/'original-converter.c'
        extracted_reader.write_text('#include <darling/emulation/common/simple.h>\n'+reader)
        extracted_converter.write_text('#include <darling/emulation/conversion/stat/common.h>\n'+converter+'\n')
        def run(command):
            commands.append([str(value) for value in command])
            subprocess.run(command, check=True, timeout=120)
        originals = []
        for path in sources+[extracted_reader, extracted_converter]:
            obj = output/(path.stem+'.o')
            run(['clang', '-O3', '-fPIC', '-fvisibility=hidden', '-I'+str(headers), '-c', str(path), '-o', str(obj)])
            originals.append(obj)
        rust_objects = [runtime/'objects/paths.o', runtime/'objects/errno.o']
        for variant in ('original', 'rust'):
            adapters = [] if variant == 'original' else [fixture_dir/'path-unused-boundaries-native.c', fixture_dir/'copy-signal-native.c']
            objects = originals+[runtime/'objects/errno.o'] if variant == 'original' else rust_objects
            run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', str(fixture), *map(str, adapters), *map(str, objects), '-o', str(output/variant)])
        report = dict(scope=__doc__, source_root=str(source), runtime_root=str(runtime),
            source_pin=XNU_PIN, source_sha256=hashes,
            extraction={str(helper_sources[0]):reader_range, str(helper_sources[1]):converter_range},
            fixture_sha256={str(path):digest(path) for path in [fixture, fixture_dir/'path-unused-boundaries-native.c', fixture_dir/'copy-signal-native.c']},
            rust_objects_sha256={str(path):digest(path) for path in rust_objects},
            generated_sha256={str(path):digest(path) for path in sorted(headers.rglob('*')) if path.is_file()} | {str(path):digest(path) for path in [extracted_reader, extracted_converter]},
            binaries_sha256={str(output/name):digest(output/name) for name in ('original', 'rust')},
            commands=commands)
        (output/'benchmark-build.json').write_text(json.dumps(report, indent=2)+'\n')
    if args.build_only:
        print(output/'benchmark-build.json')
        return
    allowed = sorted(os.sched_getaffinity(0))
    records = []
    try:
        os.sched_setaffinity(0, {allowed[0]})
        for sample in range(args.samples):
            for variant in (('original', 'rust') if sample % 2 == 0 else ('rust', 'original')):
                command = [str(output/variant), str(args.iterations)]
                rounds, cpu, wall = map(int, subprocess.check_output(command, text=True, timeout=120).split())
                if rounds != args.iterations:
                    raise ValueError('fixture reported unexpected iteration count')
                records.append(dict(sample=sample, variant=variant, roundtrips=rounds, cpu_ns=cpu, wall_ns=wall))
    finally:
        os.sched_setaffinity(0, set(allowed))
    summary = {}
    for variant in ('original', 'rust'):
        samples = [record for record in records if record['variant'] == variant]
        values = [record['cpu_ns']/record['roundtrips'] for record in samples]
        quartiles = statistics.quantiles(values, n=4, method='inclusive')
        summary[variant] = dict(median_cpu_ns_per_roundtrip=statistics.median(values),
            cpu_q1_ns=quartiles[0], cpu_q3_ns=quartiles[2],
            median_wall_ns_per_roundtrip=statistics.median(record['wall_ns']/record['roundtrips'] for record in samples))
    summary['cpu_change_percent'] = 100*(summary['rust']['median_cpu_ns_per_roundtrip']/summary['original']['median_cpu_ns_per_roundtrip']-1)
    report.update(samples=args.samples, iterations=args.iterations, allowed_affinity=allowed,
                  pinned_cpu=allowed[0], records=records, summary=summary)
    (output/'benchmark.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
