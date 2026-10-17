#!/usr/bin/env python3
"""Run pinned real guarded-table/close consumers through Darling's futex ABI.

Native x86-64 Linux execution, not a macOS process test. Production C files are
read from pinned Git blobs. The original raw syscall assembly changes only its
Mach-O symbol/directive spelling for ELF. No runtime subsystem is substituted;
unused close.c cancellation/logging sections are removed by linker GC. The test
harness supplies host TLS cerror and panic abort adapters explicitly.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tempfile

from check_errno import DARLING_PIN, XNU_PIN, EMULATION, SDK, extract, run as checked_run


def run(args):
    try:
        return checked_run(args)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Command failed: {error.cmd}\n{error.stdout}\n{error.stderr}") from error


def verify(args):
    objects = [path.resolve(strict=True) for path in
               [args.libsimple_object, args.futex_object, args.errno_object]]
    with tempfile.TemporaryDirectory(prefix='darling-runtime-') as directory:
        work = Path(directory)
        inputs = {}
        def take(repo, pin, source, destination):
            inputs[source] = extract(repo, pin, source, work/destination)
        headers = ['common/base.h', 'common/guarded/table.h', 'common/simple.h',
                   'conversion/errno.h', 'conversion/duct_errno.h',
                   'linux_premigration/ext/futex.h',
                   'linux_premigration/linux-syscalls/linux.h',
                   'linux_premigration/linux-syscalls/linux-x86_64.h',
                   'xnu_syscall/bsd/impl/unistd/close.h',
                   'xnu_syscall/bsd/helper/bsdthread/cancelable.h',
                   'xnu_syscall/bsd/impl/bsdthread/pthread_canceled.h', 'other/mach/lkm.h']
        for name in headers:
            take(args.xnu, XNU_PIN, EMULATION+'/include/'+name, 'include/darling/emulation/'+name)
        take(args.xnu, XNU_PIN, 'libsyscall/os/tsd.h', 'include/os/tsd.h')
        for name in ['base.h', 'lock.h']:
            take(args.upstream, DARLING_PIN, 'src/libsimple/include/libsimple/'+name, 'include/libsimple/'+name)
        take(args.upstream, DARLING_PIN, 'src/libsimple/src/lock.c', 'lock.c')
        sources = {'table.c': 'common/guarded/table.c',
                   'close.c': 'xnu_syscall/bsd/impl/unistd/close.c',
                   'futex.c': 'linux_premigration/ext/futex.c',
                   'errno.c': 'conversion/errno.c',
                   'linux-syscall.S': 'linux_premigration/linux-syscall.S'}
        for destination, source in sources.items():
            take(args.xnu, XNU_PIN, EMULATION+'/src/'+source, destination)
        # Keep Darwin errno ABI isolated from the Linux host harness headers.
        for name in ['errno.h', 'cdefs.h', '_types/_errno_t.h']:
            take(args.xnu, XNU_PIN, 'bsd/sys/'+name, 'darwin/sys/'+name)
        for name in ['_symbol_aliasing.h', '_posix_availability.h']:
            take(args.upstream, DARLING_PIN, SDK+'/'+name, 'darwin/sys/'+name)
        raw = (work/'linux-syscall.S').read_text()
        raw = raw.replace('.private_extern _linux_syscall', '.hidden linux_syscall')
        raw = raw.replace('_linux_syscall', 'linux_syscall')
        raw += '\n.section .note.GNU-stack,"",@progbits\n'
        (work/'linux-syscall-elf.S').write_text(raw)
        run(['clang', '-c', work/'linux-syscall-elf.S', '-o', work/'syscall.o'])
        common = ['clang', '-O2', '-std=gnu11', '-ffunction-sections', '-fdata-sections',
                  '-I', work/'include']
        for name in ['table', 'close', 'futex', 'errno']:
            flags = common[:]
            if name in ['close', 'errno']:
                flags += ['-I', work/'darwin', '-include', work/'darwin/sys/errno.h']
            run([*flags, '-c', work/(name+'.c'), '-o', work/(name+'.o')])
        run([*common, '-DLIBSIMPLE_DARLING=1', '-c', work/'lock.c', '-o', work/'lock.o'])
        fixtures = Path(__file__).resolve().parent/'fixtures'
        driver = fixtures/'darwin-runtime.c'
        abi = Path(__file__).resolve().parent.parent/'crates/darling-libsimple/tests/abi.c'
        cases = [('original', [work/'lock.o', work/'futex.o', work/'errno.o']),
                 ('rust', objects)]
        results = {}
        for name, implementation in cases:
            executable = work/name
            run([*common, '-pthread', driver, work/'table.o', work/'close.o',
                 work/'syscall.o', *implementation, '-Wl,--gc-sections', '-o', executable])
            results[name] = run([executable]).stdout.strip()
            # Reuse comprehensive ABI stress (once/rwlock/condvar REQUEUE), with
            # the same real raw-syscall/futex backend and host boundary adapters.
            adapters = work/'adapters.c'
            adapters.write_text('#include <stdlib.h>\nlong cerror(int e) { (void)e; abort(); }\n'
                                '_Noreturn void __simple_abort(void) { abort(); }\n')
            run([*common, '-pthread', abi, adapters, work/'syscall.o',
                 *implementation, '-Wl,--gc-sections', '-o', work/(name+'-abi')])
            results[name+'_abi'] = run([work/(name+'-abi')]).stdout.strip()
        if results['original'] != results['rust'] or results['original_abi'] != results['rust_abi']:
            raise ValueError('pinned C/Rust runtime consumer output mismatch: '+str(results))
        return {
            'scope': 'Actual pinned guard_table.c and full close.c linked with section GC, native Linux x86_64 execution through Darling futex ABI; not macOS application startup',
            'darling_pin': DARLING_PIN, 'xnu_pin': XNU_PIN, 'input_sha256': inputs,
            'source_changes': 'Only linux-syscall.S Mach-O symbol/directive spelling adapted to ELF; C files unmodified',
            'test_adapters': 'Host _Thread_local Darwin errno cerror and abort-backed __simple_abort; no emulation subsystem stubs',
            'counts': {'direct_futex_calls': 22, 'guard_table_thread_cycles': 120000,
                       'fork_children': 1, 'capacity_slots': 1024,
                       'note': 'Additional lock/condvar futex syscall counts vary with scheduling and are not measured'},
            'consumer': results['rust'], 'abi': results['rust_abi'], 'differential': 'identical pinned C and Rust output',
            'objects': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in objects},
            'compiler': run(['clang', '--version']).stdout.splitlines()[0], 'platform': platform.platform(),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--xnu', type=Path, required=True)
    parser.add_argument('--libsimple-object', type=Path, required=True)
    parser.add_argument('--futex-object', type=Path, required=True)
    parser.add_argument('--errno-object', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    record = verify(args)
    if args.report:
        args.report.write_text(json.dumps(record, indent=2)+'\n')
    print(record['consumer'])
    print(record['abi'])
    print(record['differential'])


if __name__ == '__main__':
    main()
