#!/usr/bin/env python3
"""Compare exact original C and Rust file operations with native boundary adapters.

The source files are read unchanged; generated include shims expose their raw
syscall/virtual-root/descriptor boundaries for native execution. This does not
measure Darling virtual-root expansion, guest TLS or whole-app performance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

from check_errno import XNU_PIN


def pinned_hashes(source, paths):
    xnu = source/'src/external/xnu'
    result = {}
    for path in paths:
        relative = path.relative_to(xnu).as_posix()
        pinned = subprocess.check_output(['git', '-C', str(xnu), 'show', XNU_PIN+':'+relative])
        if path.read_bytes() != pinned:
            raise ValueError('reference file differs from pinned Git blob: '+str(path))
        result[str(path)] = hashlib.sha256(pinned).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','runtime','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--samples',type=int,default=21)
    parser.add_argument('--iterations',type=int,default=2000)
    args=parser.parse_args()
    if args.samples<3 or args.iterations<1:
        parser.error('at least three samples and positive iterations required')
    source=args.source.resolve(strict=True);runtime=args.runtime.resolve(strict=True)
    args.output=args.output.resolve()
    if args.output.is_relative_to(source) or source.is_relative_to(args.output):
        parser.error('output must be separate from reference source')
    args.output.mkdir(parents=True,exist_ok=False)
    headers=args.output/'headers';headers.mkdir()
    base=source/'src/external/xnu/darling/src/libsystem_kernel/emulation'
    names=('unlinkat','linkat','renameat')
    sources=[base/'src/xnu_syscall/bsd/impl/unistd'/(n+'.c') for n in names]
    inputs=pinned_hashes(source,[*sources,base/'include/conversion/common_at.h'])
    for p in sources:
        for line in p.read_text().splitlines():
            if line.startswith('#include <darling/'):
                relative=line.split('<',1)[1].split('>',1)[0]
                target=headers/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('/* Explicit native boundary adapter header. */\n')
    def header(name,text):
        target=headers/'darling/emulation'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
    header('common/base.h','''#include <sys/syscall.h>
extern long linux_syscall(long,long,long,long,long,long,int);
#define SC3(n,a,b,c) linux_syscall((long)(a),(long)(b),(long)(c),0,0,0,n)
#define SC4(n,a,b,c,d) linux_syscall((long)(a),(long)(b),(long)(c),(long)(d),0,0,n)
#define SC5(n,a,b,c,d,e) linux_syscall((long)(a),(long)(b),(long)(c),(long)(d),(long)(e),0,n)
#define PICK(_1,_2,_3,_4,_5,NAME,...) NAME
#define LINUX_SYSCALL(n,...) PICK(__VA_ARGS__,SC5,SC4,SC3)(n,__VA_ARGS__)
''')
    header('conversion/common_at.h',(base/'include/conversion/common_at.h').read_text())
    header('conversion/errno.h','extern int errno_linux_to_bsd(int);\n')
    header('linux_premigration/vchroot_expand.h','struct vchroot_expand_args {char path[4096]; unsigned flags; int dfd;};\n#define VCHROOT_FOLLOW 1\nextern int vchroot_expand(struct vchroot_expand_args *);\n')
    commands=[]
    for name,path in zip(names,sources):
        command=['clang','-O3','-fPIC','-I'+str(headers),'-c',str(path),'-o',str(args.output/(name+'.o'))]
        subprocess.run(command,check=True);commands.append(command)
    driver=Path(__file__).parent/'fixtures/fileops-bench.c'
    binaries={}
    for variant in ('original','rust'):
        objects=[args.output/(n+'.o') if variant=='original' else runtime/'objects'/(n+'.o') for n in names]
        if variant=='original':objects.append(runtime/'objects/errno.o')
        command=['clang','-O2','-Wall','-Wextra','-Werror',str(driver),*[str(p) for p in objects],'-o',str(args.output/variant)]
        subprocess.run(command,check=True);commands.append(command);binaries[variant]=args.output/variant
    cpu=min(os.sched_getaffinity(0));os.sched_setaffinity(0,{cpu});records=[]
    for sample in range(args.samples):
        for variant in (('original','rust') if sample%2==0 else ('rust','original')):
            calls,cpu_ns,wall_ns=map(int,subprocess.check_output([str(binaries[variant]),str(args.iterations)],text=True).split())
            records.append(dict(sample=sample,variant=variant,roundtrips=calls,cpu_ns=cpu_ns,wall_ns=wall_ns))
    summary={}
    for variant in binaries:
        matching=[r for r in records if r['variant']==variant];times=[r['cpu_ns']/r['roundtrips'] for r in matching]
        q=statistics.quantiles(times,n=4,method='inclusive')
        summary[variant]={'median_cpu_ns_per_roundtrip':statistics.median(times),'cpu_q1_ns_per_roundtrip':q[0],'cpu_q3_ns_per_roundtrip':q[2],
                          'median_wall_ns_per_roundtrip':statistics.median(r['wall_ns']/r['roundtrips'] for r in matching)}
    summary['cpu_change_percent']=100*(summary['rust']['median_cpu_ns_per_roundtrip']/summary['original']['median_cpu_ns_per_roundtrip']-1)
    report={'scope':__doc__,'roundtrip':'linkat -> renameat -> unlinkat; actual temporary native files','samples':args.samples,'iterations':args.iterations,'cpu':cpu,'commands':commands,
            'source_pin':XNU_PIN,'source_sha256':inputs,'native_adapters':{str(p.relative_to(headers)):p.read_text() for p in headers.rglob('*') if p.is_file()},
            'rust_objects_sha256':{str(runtime/'objects'/(n+'.o')):hashlib.sha256((runtime/'objects'/(n+'.o')).read_bytes()).hexdigest() for n in names},
            'records':records,'summary':summary}
    (args.output/'benchmark.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
