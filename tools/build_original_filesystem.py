#!/usr/bin/env python3
"""Build an exact pinned C filesystem timing reference in an owned directory.

Retains matching compile/link flags and surrounding runtime. An include overlay
uses the pinned common/base.h syscall macros, bypassing the local __lolsys_syscall
trace wrapper so the timed C and Rust entries both call linux_syscall directly.
No reference source, build tree or installation is changed.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
from benchmark_fileops import pinned_hashes
from relink_system_kernel import KERNEL, digest, symbols


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','build','linked','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--compat-object',type=Path,required=True,
                        help='Retained matching-runtime public mount-name extension')
    args = parser.parse_args()
    source,build,linked = (p.resolve(strict=True) for p in (args.source,args.build,args.linked))
    output = args.output.resolve()
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (source,build)):
        parser.error('output must be separate from reference source/build')
    output.mkdir(parents=True,exist_ok=False)
    base = source/KERNEL/'emulation'
    directory = build/KERNEL/'emulation'
    flags = {}
    for line in (directory/'CMakeFiles/emulation.dir/flags.make').read_text().splitlines():
        key,sep,value = line.partition(' = ')
        if sep and key in ('C_DEFINES','C_INCLUDES','C_FLAGS'):
            flags[key] = shlex.split(value)
    if len(flags)!=3:
        raise ValueError('missing matching C flags')
    sources = [base/'src/xnu_syscall/bsd/impl/stat'/f'{name}.c' for name in ('statfs','fstatfs')]
    header = base/'include/common/base.h'
    hashes = pinned_hashes(source,[*sources,header])
    include = output/'include'
    overlay = include/'darling/emulation/common/base.h'
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(header.read_bytes())
    commands,objects = [],{}
    def run(command,cwd):
        commands.append([str(x) for x in command])
        subprocess.run(command,cwd=cwd,check=True,timeout=120)
    for path in sources:
        obj = output/(path.name+'.o')
        run(['clang',*flags['C_DEFINES'],'-I'+str(include),*flags['C_INCLUDES'],*flags['C_FLAGS'],'-c',str(path),'-o',str(obj)],directory)
        if not symbols(obj,'--defined-only'):
            raise ValueError('missing original definitions')
        objects[path.name] = obj
    inputs = shlex.split((linked/'original.rsp').read_text())
    replacements = {}
    for name,obj in objects.items():
        matches = [x for x in inputs if x.endswith('/stat/'+name+'.o')]
        if len(matches)!=1:
            raise ValueError('original object must occur exactly once: '+name)
        replacements[matches[0]] = str(obj)
    response = output/'original.rsp'
    compat = args.compat_object.resolve(strict=True)
    if symbols(compat,'--defined-only') != {'_lolsys_fill_mount_names'}:
        raise ValueError('unexpected matching filesystem compatibility bridge')
    response.write_text(shlex.join([replacements.get(x,x) for x in inputs]+[str(compat)])+'\n')
    command = json.loads((linked/'original-argv.json').read_text())
    target = output/'libsystem_kernel-original.dylib'
    for i,arg in enumerate(command):
        if arg.startswith('@'):
            command[i] = '@'+str(response)
        elif i and command[i-1]=='-o':
            command[i] = str(target)
        elif arg.startswith('-Wl,-map,'):
            command[i] = '-Wl,-map,'+str(output/'original.map')
    run(command,build/KERNEL)
    for option in ('--defined-only','--undefined-only'):
        if symbols(target,option)!=symbols(linked/'libsystem_kernel-original.dylib',option):
            raise ValueError('reference symbol set changed')
    record = dict(scope=__doc__,source_sha256=hashes,flags=flags,commands=commands,replacements=replacements,objects={str(p):digest(p) for p in [*objects.values(),compat]},kernel_sha256=digest(target),public_symbol_sets_identical=True,import_symbol_sets_identical=True,previous_kernel_sha256=digest(linked/'libsystem_kernel-original.dylib'))
    (output/'verification.json').write_text(json.dumps(record,indent=2)+'\n')
    print('Exact original filesystem reference linked',digest(target))


if __name__ == '__main__':
    main()
