#!/usr/bin/env python3
"""Compile generated wrappers using the pinned ABI header, then link Mach-O.

Requires cc, clang, ld64.lld and a local pinned Darling object repository.
This checks build artifacts only; it does not run a macOS application.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

PIN = '60ba801decee7a00782f74f6be4c8ffb013f79ff'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--wrapgen', type=Path, required=True)
    parser.add_argument('--elfdep', type=Path, required=True)
    args = parser.parse_args()
    header = subprocess.check_output(['git', '-C', str(args.upstream), 'show', f'{PIN}:src/startup/mldr/elfcalls/elfcalls.h'])
    with tempfile.TemporaryDirectory(prefix='darling-wrapper-macho-') as name:
        work = Path(name)
        (work / 'elfcalls.h').write_bytes(header)
        (work / 'fixture.c').write_text('int sample_variable=42; int sample_function(void){return sample_variable;}\n')
        subprocess.run(['cc', '-shared', '-fPIC', '-Wl,-soname,libdarlingfixture.so', str(work/'fixture.c'), '-o', str(work/'input.so')], check=True)
        subprocess.run([str(args.wrapgen.resolve()), str(work/'input.so'), str(work/'wrapper.c'), str(work/'vars.h')], check=True)
        subprocess.run(['clang', '-target', 'x86_64-apple-macosx10.12', '-ffreestanding', '-I', str(work), '-c', str(work/'wrapper.c'), '-o', str(work/'wrapper.o')], check=True)
        subprocess.run(['ld64.lld', '-dylib', '-arch', 'x86_64', '-platform_version', 'macos', '10.12', '10.12', '-undefined', 'dynamic_lookup', '-o', str(work/'wrapped.dylib'), str(work/'wrapper.o')], check=True)
        result = subprocess.check_output([str(args.elfdep.resolve()), str(work/'wrapped.dylib')])
        if result != b'libdarlingfixture.so\n':
            raise RuntimeError(f'unexpected linked wrapper ELF dependency: {result!r}')
        print('Generated C compiled with pinned elfcalls ABI header; linked x86-64 Mach-O dependency matches fixture.')


if __name__ == '__main__':
    main()
