#!/usr/bin/env python3
"""Compare unchanged pinned C and Rust process services through native Linux adapters.

Each round requests 32 and 256 entropy bytes and reads the current thread ID.
Identical fixture, raw syscall adapter and shared errno table; no Darling guest
startup, TLS, server or whole-application performance claim.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
from benchmark_fileops import pinned_hashes
from relink_system_kernel import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'runtime', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--samples', type=int, default=21)
    parser.add_argument('--iterations', type=int, default=100000)
    args = parser.parse_args()
    if args.samples < 3 or args.iterations < 1:
        parser.error('require at least three samples and positive iterations')
    source, runtime = args.source.resolve(strict=True), args.runtime.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        parser.error('output must be separate from reference')
    output.mkdir(parents=True, exist_ok=False)
    headers = output/'headers'
    base = source/'src/external/xnu/darling/src/libsystem_kernel/emulation'
    sources = [base/'src/xnu_syscall/bsd/impl/misc'/f'{name}.c' for name in ('getentropy','thread_selfid')]
    hashes = pinned_hashes(source, sources)
    for path in sources:
        for line in path.read_text().splitlines():
            if line.startswith('#include <darling/'):
                target = headers/line.split('<',1)[1].split('>',1)[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('/* Native boundary adapter. */\n')
    def header(relative, contents):
        (headers/'darling/emulation'/relative).write_text(contents)
    header('common/base.h', '''#include <sys/syscall.h>
long linux_syscall(long,long,long,long,long,long,int);
#define VISIBLE __attribute__((visibility("default")))
#define S0(n) linux_syscall(0,0,0,0,0,0,n)
#define S1(n,a) linux_syscall((long)(a),0,0,0,0,0,n)
#define S2(n,a,b) linux_syscall((long)(a),(long)(b),0,0,0,0,n)
#define S3(n,a,b,c) linux_syscall((long)(a),(long)(b),(long)(c),0,0,0,n)
#define PICK(_0,_1,_2,_3,NAME,...) NAME
#define LINUX_SYSCALL(n,...) PICK(_,##__VA_ARGS__,S3,S2,S1,S0)(n,##__VA_ARGS__)
''')
    header('conversion/errno.h', 'int errno_linux_to_bsd(int);\n')
    header('conversion/duct_errno.h', '#define EIO 5\n')
    commands = []
    def run(command):
        commands.append([str(x) for x in command])
        subprocess.run(command, check=True, timeout=120)
    for path in sources:
        run(['clang','-O3','-fPIC','-fvisibility=hidden','-I'+str(headers),'-c',str(path),'-o',str(output/(path.stem+'.o'))])
    fixture = Path(__file__).parent/'fixtures/services-bench.c'
    for variant in ('original','rust'):
        objects = [output/'getentropy.o',output/'thread_selfid.o',runtime/'objects/errno.o'] if variant == 'original' else [runtime/'objects/process-ids.o']
        run(['clang','-O2','-Wall','-Wextra','-Werror',str(fixture),*map(str,objects),'-o',str(output/variant)])
    allowed = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0,{allowed[0]})
    records = []
    for sample in range(args.samples):
        for variant in (('original','rust') if sample % 2 == 0 else ('rust','original')):
            rounds,cpu,wall = map(int,subprocess.check_output([str(output/variant),str(args.iterations)],text=True).split())
            records.append(dict(sample=sample,variant=variant,roundtrips=rounds,cpu_ns=cpu,wall_ns=wall))
    summary = {}
    for variant in ('original','rust'):
        samples = [r for r in records if r['variant']==variant]
        values = [r['cpu_ns']/r['roundtrips'] for r in samples]
        q = statistics.quantiles(values,n=4,method='inclusive')
        summary[variant] = dict(median_cpu_ns_per_roundtrip=statistics.median(values),cpu_q1_ns=q[0],cpu_q3_ns=q[2],median_wall_ns_per_roundtrip=statistics.median(r['wall_ns']/r['roundtrips'] for r in samples))
    summary['cpu_change_percent'] = 100*(summary['rust']['median_cpu_ns_per_roundtrip']/summary['original']['median_cpu_ns_per_roundtrip']-1)
    report = dict(scope=__doc__,allowed_affinity=allowed,pinned_cpu=allowed[0],source_sha256=hashes,fixture_sha256=digest(fixture),object_sha256=digest(runtime/'objects/process-ids.o'),commands=commands,records=records,summary=summary)
    (output/'benchmark.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
