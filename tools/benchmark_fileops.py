#!/usr/bin/env python3
"""Compare exact original C and Rust runtime operations with native boundary adapters.

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
    parser.add_argument('--family', choices=['mutation','permissions','fdops','descriptors','identifiers','control','paths','metadata'], default='mutation')
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
    paths = {'unlinkat':'unistd/unlinkat.c', 'linkat':'unistd/linkat.c', 'renameat':'unistd/renameat.c'} if args.family=='mutation' else {'mkdirat':'stat/mkdirat.c', 'faccessat':'unistd/faccessat.c', 'fchmodat':'unistd/fchmodat.c'}
    if args.family in ('fdops','descriptors'):
        descriptor_names=['fchmod','ftruncate','fsync','fdatasync']
        if args.family=='descriptors':
            descriptor_names += ['lseek','flock']
        paths={n:'unistd/'+n+'.c' for n in descriptor_names}
    if args.family=='identifiers':
        paths={n:'unistd/'+n+'.c' for n in ['getpid','getppid','getpgrp','getpgid','getsid']}
    if args.family=='control':
        paths={n:'unistd/'+n+'.c' for n in ['setpgid','setsid','umask','getdtablesize']}
    if args.family=='paths':
        paths={'mkdirat':'stat/mkdirat.c','rmdir':'stat/rmdir.c','truncate':'unistd/truncate.c'}
    if args.family=='metadata':
        paths={'stat':'stat/stat.c','fstat':'stat/fstat.c'}
    names=tuple(paths)
    sources=[base/'src/xnu_syscall/bsd/impl'/path for path in paths.values()]
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
#define LL_ARG(x) (x)
#define LINUX_SYSCALL0(n) linux_syscall(0,0,0,0,0,0,n)
#define LINUX_SYSCALL1(n,a) linux_syscall((long)(a),0,0,0,0,0,n)
#define LINUX_SYSCALL2(n,a,b) linux_syscall((long)(a),(long)(b),0,0,0,0,n)
#define SC2(n,a,b) LINUX_SYSCALL2(n,a,b)
#define SC3(n,a,b,c) linux_syscall((long)(a),(long)(b),(long)(c),0,0,0,n)
#define SC4(n,a,b,c,d) linux_syscall((long)(a),(long)(b),(long)(c),(long)(d),0,0,n)
#define SC5(n,a,b,c,d,e) linux_syscall((long)(a),(long)(b),(long)(c),(long)(d),(long)(e),0,n)
#define PICK(_1,_2,_3,_4,_5,NAME,...) NAME
#define LINUX_SYSCALL(n,...) PICK(__VA_ARGS__,SC5,SC4,SC3,SC2,LINUX_SYSCALL1)(n,__VA_ARGS__)
''')
    header('conversion/common_at.h',(base/'include/conversion/common_at.h').read_text())
    header('conversion/errno.h','extern int errno_linux_to_bsd(int);\n')
    header('linux_premigration/vchroot_expand.h','struct vchroot_expand_args {char path[4096]; unsigned flags; int dfd;};\n#define VCHROOT_FOLLOW 1\nextern int vchroot_expand(struct vchroot_expand_args *);\n')
    if args.family=='permissions':
        header('conversion/stat/common.h', '''struct linux_stat {unsigned long st_dev,st_ino,st_nlink;unsigned st_mode,st_uid,st_gid,pad;unsigned long st_rdev;long st_size,st_blksize,st_blocks,atime,atime_nsec,mtime,mtime_nsec,ctime,ctime_nsec,unused[3];};
_Static_assert(sizeof(struct linux_stat)==144,"Linux stat size");
_Static_assert(__builtin_offsetof(struct linux_stat,st_mode)==24,"Linux mode offset");
''')
    if args.family in ('fdops','descriptors'):
        original_header=base/'include/xnu_syscall/bsd/impl/unistd/fsync.h'
        inputs.update(pinned_hashes(source,[original_header]))
        header('xnu_syscall/bsd/impl/unistd/fsync.h',original_header.read_text())
    if args.family=='control':
        for relative in ('xnu_syscall/bsd/impl/misc/getrlimit.h', 'conversion/misc/getrlimit.h'):
            original_header=base/'include'/relative
            inputs.update(pinned_hashes(source,[original_header]))
            header(relative,original_header.read_text())
        # Audit the retained NOFILE -1 policy even though this native adapter
        # uses host getrlimit instead of guest prlimit64/TLS runtime paths.
        inputs.update(pinned_hashes(source,[base/'src/xnu_syscall/bsd/impl/misc/getrlimit.c']))
    if args.family=='paths':
        header('common/bsdthread/per_thread_wd.h','extern int get_perthread_wd(void);\n')
        for name,relative in paths.items():
            original_header=base/'include/xnu_syscall/bsd/impl'/relative.replace('.c','.h')
            inputs.update(pinned_hashes(source,[original_header]))
            header('xnu_syscall/bsd/impl/'+relative.replace('.c','.h'),original_header.read_text())
        # Original rmdir/truncate rely on the full build's string declaration.
        header('common/base.h',(headers/'darling/emulation/common/base.h').read_text()+'\n#include <string.h>\n')
    if args.family=='metadata':
        header('common/bsdthread/per_thread_wd.h','extern int get_perthread_wd(void);\n')
        for relative in ('stat/stat.h','stat/fstat.h','stat/fstatat.h'):
            original_header=base/'include/xnu_syscall/bsd/impl'/relative
            inputs.update(pinned_hashes(source,[original_header]))
            header('xnu_syscall/bsd/impl/'+relative,original_header.read_text())
        header('conversion/stat/common.h',"""struct linux_stat {unsigned long storage[18];};
struct stat {unsigned char opaque[256];};
struct stat64 {unsigned char opaque[256];};
_Static_assert(sizeof(struct linux_stat)==144,"Linux stat size");
void stat_linux_to_bsd(const struct linux_stat *,struct stat *);
void stat_linux_to_bsd64(const struct linux_stat *,struct stat64 *);
""")
    commands=[]
    for name,path in zip(names,sources):
        command=['clang','-O3','-fPIC','-fvisibility=hidden','-I'+str(headers),'-c',str(path),'-o',str(args.output/(name+'.o'))]
        subprocess.run(command,check=True);commands.append(command)
    driver=Path(__file__).parent/'fixtures'/('fileops-bench.c' if args.family=='mutation' else 'permissions-bench.c')
    if args.family=='fdops':
        driver=Path(__file__).parent/'fixtures/fdops-bench.c'
    if args.family=='descriptors':
        driver=Path(__file__).parent/'fixtures/descriptors-bench.c'
    if args.family=='identifiers':
        driver=Path(__file__).parent/'fixtures/identifiers-bench.c'
    if args.family=='control':
        driver=Path(__file__).parent/'fixtures/control-bench.c'
    if args.family=='paths':
        driver=Path(__file__).parent/'fixtures/paths-bench.c'
    if args.family=='metadata':
        driver=Path(__file__).parent/'fixtures/metadata-bench.c'
    rust_objects=[runtime/'objects'/(n+'.o') for n in names]
    if args.family=='descriptors':
        rust_objects=[runtime/'objects/fd.o']
    if args.family=='identifiers':
        rust_objects=[runtime/'objects/process-ids.o']
    if args.family=='control':
        rust_objects=[runtime/'objects/fd.o',runtime/'objects/process-ids.o',runtime/'objects/errno.o']
    if args.family in ('paths','metadata'):
        rust_objects=[runtime/'objects/paths.o']
    shared_backend=None
    if args.family=='metadata':
        shared_backend=args.output/'shared-rust-metadata-backend.o'
        command=['llvm-objcopy',*[f'--strip-symbol={name}' for name in ('sys_stat','sys_stat64','sys_fstat','sys_fstat64')],str(runtime/'objects/paths.o'),str(shared_backend)]
        subprocess.run(command,check=True);commands.append(command)
    binaries={}
    for variant in ('original','rust'):
        objects=[args.output/(n+'.o') for n in names] if variant=='original' else rust_objects
        if variant=='original':
            objects.append(runtime/'objects/errno.o')
            if shared_backend is not None:objects.append(shared_backend)
        command=['clang','-O2','-Wall','-Wextra','-Werror','-I'+str(headers),str(driver),*[str(p) for p in objects],'-o',str(args.output/variant)]
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
    roundtrip = ('linkat -> renameat -> unlinkat' if args.family=='mutation' else ('mkdirat -> fchmodat -> faccessat -> unlinkat' if args.family=='permissions' else 'fchmod -> ftruncate -> fsync -> fdatasync'))
    if args.family=='descriptors':
        roundtrip='fchmod(0600) -> ftruncate(128) -> lseek(64, SEEK_SET) -> flock(EX|NB) -> flock(UN) -> fsync -> fdatasync; warmed same file without new contents'
    if args.family=='identifiers':
        roundtrip='getpid -> getppid -> getpgrp -> getpgid(0) -> getsid(0); real native process identifiers, stable checksum checked against libc'
    elif args.family=='control':
        roundtrip='setpgid(0,0) success -> setsid EPERM -> umask(022) warmed prior022 -> getdtablesize retained NOFILE-1/clamp; owned group-leader child only; warm mutator/error paths, not successful session-creation throughput'
    elif args.family=='paths':
        roundtrip='mkdirat -> rmdir -> truncate(128); owned absolute directory/file paths; no-op native virtual-root/TLS adapters; retained initialized in-place path buffers'
    elif args.family=='metadata':
        roundtrip='fstat -> fstat64 -> stat -> stat64; owned absolute file path, raw Linux metadata prefix copied120/144 bytes into opaque256-byte guarded adapters; original variant uses original C entry wrappers plus shared Rust fstatat backend, not an all-C chain; Rust variant uses all-Rust entry wrappers; no native Darwin layout claim'
    else:
        roundtrip+='; actual temporary native files'
    report={'scope':__doc__,'roundtrip':roundtrip,'samples':args.samples,'iterations':args.iterations,'cpu':cpu,'commands':commands,
            'source_pin':XNU_PIN,'source_sha256':inputs,'native_adapters':{str(p.relative_to(headers)):p.read_text() for p in headers.rglob('*') if p.is_file()},
            'rust_objects_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in rust_objects},
            'records':records,'summary':summary}
    if shared_backend is not None:
        report['shared_backend_sha256']=hashlib.sha256(shared_backend.read_bytes()).hexdigest()
    (args.output/'benchmark.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
