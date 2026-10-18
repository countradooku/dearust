#!/usr/bin/env python3
"""Compare exact pinned C stream wrappers with Rust using native syscall adapters.

Identical four-call /dev/zero -> /dev/null workload; cancellation adapter returns
not-canceled rather than measuring actual Darling TLS/RPC. Guest evidence is
required separately. No source or installed runtime is modified.
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
    sources = [base/'src/xnu_syscall/bsd/impl/unistd'/f'{name}.c' for name in ('read','write')]
    hashes = pinned_hashes(source, sources)
    for path in sources:
        for line in path.read_text().splitlines():
            if line.startswith('#include <darling/'):
                path = headers/line.split('<',1)[1].split('>',1)[0]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('/* Native boundary adapter. */\n')
    def header(relative, text):
        (headers/'darling/emulation'/relative).write_text(text)
    header('common/base.h', '''#include <sys/syscall.h>
long linux_syscall(long,long,long,long,long,long,int);
#define LINUX_SYSCALL3(n,a,b,c) linux_syscall((long)(a),(long)(b),(long)(c),0,0,0,n)
''')
    header('conversion/errno.h', 'int errno_linux_to_bsd(int);\n')
    header('conversion/duct_errno.h', '#define LINUX_EIO 5\n')
    header('linux_premigration/misc/ioctl.h', 'long __real_ioctl(int,int,void*);\n')
    header('xnu_syscall/bsd/helper/bsdthread/cancelable.h', '''long sys_pthread_canceled(int);
#define CANCELATION_POINT() if(sys_pthread_canceled(0)==0)return -4
''')
    for name in ('read','write'):
        original_header = base/'include/xnu_syscall/bsd/impl/unistd'/f'{name}.h'
        hashes.update(pinned_hashes(source, [original_header]))
        header('xnu_syscall/bsd/impl/unistd/'+name+'.h', original_header.read_text())
    commands = []
    def run(command):
        commands.append([str(x) for x in command])
        subprocess.run(command, check=True, timeout=120)
    for path in sources:
        run(['clang','-O3','-fPIC','-fvisibility=hidden','-I'+str(headers),'-c',str(path),'-o',str(output/(path.stem+'.o'))])
    fixture = Path(__file__).parent/'fixtures/stream-bench.c'
    for variant in ('original','rust'):
        objects = [output/'read.o',output/'write.o',runtime/'objects/errno.o'] if variant == 'original' else [runtime/'objects/fd.o']
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
    report = dict(scope=__doc__,allowed_affinity=allowed,pinned_cpu=allowed[0],source_sha256=hashes,fixture_sha256=digest(fixture),object_sha256=digest(runtime/'objects/fd.o'),commands=commands,records=records,summary=summary)
    (output/'benchmark.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
