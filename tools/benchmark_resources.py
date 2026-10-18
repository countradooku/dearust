#!/usr/bin/env python3
"""Native resource get/set/getrusage comparison against exact pinned C.

Rust uses the full production paths copy helpers. Guarded and forced-kernel-copy
cohorts are reported separately. Native signal adapters support the measured
single-thread workload; this is not a Darling guest, signal/RPC integration or
whole-runtime benchmark. Each child writes only its own unchanged CORE limit.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from benchmark_fileops import pinned_hashes
from relink_system_kernel import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'runtime', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--samples', type=int, default=21)
    parser.add_argument('--iterations', type=int, default=100000)
    parser.add_argument('--buffer-layout', choices=('stack', 'heap', 'crosspage'), default='stack')
    parser.add_argument('--change-core', action='store_true', help='alternate owned child CORE soft limits0/1')
    args = parser.parse_args()
    if args.samples < 3 or args.iterations < 256:
        parser.error('require at least three samples and 256 iterations')
    source = args.source.resolve(strict=True)
    runtime = args.runtime.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        parser.error('output must be separate from reference')
    output.mkdir(parents=True, exist_ok=False)
    base = source/'src/external/xnu/darling/src/libsystem_kernel/emulation'
    sources = [base/'src/xnu_syscall/bsd/impl/misc'/f'{name}.c'
               for name in ('getrlimit', 'setrlimit', 'getrusage')]
    layout = base/'include/conversion/misc/getrlimit.h'
    hashes = pinned_hashes(source, [*sources, layout])
    headers = output/'headers'
    for path in sources:
        for line in path.read_text().splitlines():
            if line.startswith('#include <darling/'):
                target = headers/line.split('<', 1)[1].split('>', 1)[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('/* Native boundary adapter. */\n')

    def header(relative, contents):
        path = headers/'darling/emulation'/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    header('conversion/misc/getrlimit.h', layout.read_text())
    for name in ('getrlimit', 'setrlimit'):
        header(f'xnu_syscall/bsd/impl/misc/{name}.h',
               '#include <darling/emulation/conversion/misc/getrlimit.h>\n')
    header('common/base.h', '''#include <sys/syscall.h>
long linux_syscall(long,long,long,long,long,long,int);
#define S0(n) linux_syscall(0,0,0,0,0,0,n)
#define S1(n,a) linux_syscall((long)(a),0,0,0,0,0,n)
#define S2(n,a,b) linux_syscall((long)(a),(long)(b),0,0,0,0,n)
#define S3(n,a,b,c) linux_syscall((long)(a),(long)(b),(long)(c),0,0,0,n)
#define S4(n,a,b,c,d) linux_syscall((long)(a),(long)(b),(long)(c),(long)(d),0,0,n)
#define PICK(_0,_1,_2,_3,_4,NAME,...) NAME
#define LINUX_SYSCALL(n,...) PICK(_,##__VA_ARGS__,S4,S3,S2,S1,S0)(n,##__VA_ARGS__)
''')
    header('conversion/errno.h', 'int errno_linux_to_bsd(int);\n')
    commands = []

    def run(command):
        commands.append([str(x) for x in command])
        subprocess.run(command, check=True, timeout=120)

    for path in sources:
        run(['clang', '-O3', '-fPIC', '-fvisibility=hidden', '-I'+str(headers),
             '-c', str(path), '-o', str(output/(path.stem+'.o'))])
    fixture = output/'resources-bench.c'
    fixture.write_bytes((Path(__file__).parent/'fixtures/resources-bench.c').read_bytes())
    objects = runtime/'objects'
    extra = ['-DRESOURCE_BENCH_CHANGING'] if args.change_core else []
    run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-Wl,--gc-sections',
         '-DRESOURCE_BENCH_ORIGINAL', *extra, str(fixture),
         *map(str, [output/'getrlimit.o', output/'setrlimit.o', output/'getrusage.o',
                    objects/'errno.o']), '-o', str(output/'original')])
    run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-Wl,--gc-sections', *extra, str(fixture),
         *map(str, [objects/'process-ids.o', objects/'paths.o', objects/'errno.o']),
         '-o', str(output/'rust')])
    allowed = sorted(os.sched_getaffinity(0))
    records = []
    os.sched_setaffinity(0, {allowed[0]})
    cohorts = ('original', 'guarded', 'fallback')
    try:
        for sample in range(args.samples):
            order = cohorts if sample % 2 == 0 else cohorts[::-1]
            for variant in order:
                binary = 'original' if variant == 'original' else 'rust'
                started = time.perf_counter_ns()
                result = subprocess.check_output(
                    [str(output/binary), str(args.iterations), variant, args.buffer_layout], text=True)
                process_wall = time.perf_counter_ns()-started
                values = list(map(int, result.split()))
                keys = ('roundtrips', 'cpu_ns', 'wall_ns', 'cold_round_ns',
                        'sampled_p50_ns', 'sampled_p95_ns', 'sampled_p99_ns',
                        'minor_faults', 'major_faults', 'raw_ru_maxrss_kib',
                        'current_rss_kib', 'private_dirty_kib')
                if len(values) != len(keys):
                    raise ValueError('unexpected native benchmark result')
                records.append(dict(sample=sample, variant=variant,
                                    process_wall_ns=process_wall, **dict(zip(keys, values))))
    finally:
        os.sched_setaffinity(0, set(allowed))
    summary = {}
    for variant in cohorts:
        selected = [r for r in records if r['variant'] == variant]
        values = [r['cpu_ns']/r['roundtrips'] for r in selected]
        quartiles = statistics.quantiles(values, n=4, method='inclusive')
        summary[variant] = dict(
            median_cpu_ns_per_round=statistics.median(values),
            cpu_q1_ns=quartiles[0], cpu_q3_ns=quartiles[2],
            median_wall_ns_per_round=statistics.median(r['wall_ns']/r['roundtrips'] for r in selected),
            median_cold_round_ns=statistics.median(r['cold_round_ns'] for r in selected),
            median_sampled_p99_ns=statistics.median(r['sampled_p99_ns'] for r in selected),
            median_process_wall_ns=statistics.median(r['process_wall_ns'] for r in selected),
            median_minor_faults=statistics.median(r['minor_faults'] for r in selected),
            median_raw_ru_maxrss_kib=statistics.median(r['raw_ru_maxrss_kib'] for r in selected),
            median_current_rss_kib=statistics.median(r['current_rss_kib'] for r in selected),
            median_private_dirty_kib=statistics.median(r['private_dirty_kib'] for r in selected))
    for variant in ('guarded', 'fallback'):
        summary[variant]['cpu_change_percent'] = 100*(
            summary[variant]['median_cpu_ns_per_round']/summary['original']['median_cpu_ns_per_round']-1)
    report = dict(scope=__doc__, change_core=args.change_core, buffer_layout=args.buffer_layout, allowed_affinity=allowed, pinned_cpu=allowed[0],
                  caveats=['Original output retains its known Linux-layout rusage and infinity defects; Rust performs the actual Darwin conversion.',
                           'Forced fallback is an explicit policy cohort, not the ordinary production path.',
                           'ru_maxrss is the raw Linux process high-water mark and can include inherited pre-exec history; do not infer runtime memory regression from it alone.',
                           'Cold means first operation after process startup, not a dropped filesystem cache.'],
                  source_sha256=hashes, fixture_sha256=digest(fixture),
                  object_sha256={name: digest(objects/name) for name in ('process-ids.o', 'paths.o', 'errno.o')},
                  commands=commands, records=records, summary=summary)
    (output/'benchmark.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
