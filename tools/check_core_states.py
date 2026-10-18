#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture owned Linux register images and independently verify exact XNU states.

The ptrace parent owns every traced thread. No installation or global core policy
is changed. ELF fixtures contain actual kernel regsets; an additional variant has
explicit capture-host CPUID layout provenance, not a claimed kernel-emitted note.
C compiled against the supplied Darwin SDK provides every destination ABI offset.

Reproduce with --source DARLING_SOURCE --build DARLING_BUILD --converter BINARY
--output NEW_DIRECTORY. Optional --lldb BINARY records reader behavior. Adding
--reader-original C_BASELINE --reader-prior PRIOR_RUST also reproduces the i386
reader isolation on the same capture: both baselines, current, current without
raw notes, and current with GPRs only. Reader failures remain explicit evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import struct
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, commands, env=None):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True,
                            timeout=60, env=env)
    commands.append(dict(command=list(map(str, command)), returncode=result.returncode,
                         stdout=result.stdout, stderr=result.stderr))
    if result.returncode:
        raise RuntimeError('command failed: '+str(command)+'\n'+result.stderr)
    return result.stdout


def sdk_oracle(source, build, output, commands):
    fields = ('fcw', 'fsw', 'ftw', 'fop', 'ip', 'cs', 'dp', 'ds', 'mxcsr',
              'mxcsrmask', 'stmm0', 'xmm0')
    expressions = {}
    for bits in (32, 64):
        for name, flavor in (('float', 'FLOAT'), ('avx', 'AVX'), ('avx512', 'AVX512')):
            kind = f'{name}{bits}'
            expressions[kind+'_size'] = f'sizeof(x86_{name}_state{bits}_t)'
            expressions[kind+'_count'] = f'x86_{flavor}_STATE{bits}_COUNT'
        for field in fields:
            expressions[f'base{bits}_{field}'] = f'__builtin_offsetof(x86_float_state{bits}_t,__fpu_{field})'
        expressions[f'avx{bits}_ymmh'] = f'__builtin_offsetof(x86_avx_state{bits}_t,__fpu_ymmh0)'
        for field in ('ymmh0', 'k0', 'zmmh0'):
            expressions[f'avx512_{bits}_{field}'] = f'__builtin_offsetof(x86_avx512_state{bits}_t,__fpu_{field})'
    expressions['avx512_64_zmm16'] = '__builtin_offsetof(x86_avx512_state64_t,__fpu_zmm16)'
    probe = output/'xnu-layout.c'
    probe.write_text('#include <mach/i386/thread_status.h>\n'+''.join(
        f'const unsigned long long {name}=({value});\n' for name, value in expressions.items()))
    resource = subprocess.check_output(['clang', '-print-resource-dir'], text=True).strip()
    sdk = Path('Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/usr/include')
    command = ['clang', '-target', 'x86_64-apple-darwin', '-mmacosx-version-min=11.0',
               '-nostdinc', '-isystem', resource+'/include', '-DDARLING', '-DPLATFORM_MacOSX',
               '-Wno-nullability-completeness', '-Wno-undef-prefix']
    for directory in (build/'src/include', source/'src/include', source/'basic-headers',
                      source/sdk, build/sdk):
        command += ['-I', str(directory)]
    llvm = output/'xnu-layout.ll'
    run(command+['-S', '-emit-llvm', str(probe), '-o', str(llvm)], commands)
    values = {name:int(value) for name, value in re.findall(
        r'^@(\w+)\s*=.*?constant i64 (\d+)', llvm.read_text(), re.M)}
    if values.keys() != expressions.keys():
        raise ValueError('incomplete compiler ABI oracle')
    return values


def capture_layout(output, commands):
    source = output/'cpuid.c'
    source.write_text('''#include <cpuid.h>
#include <stdio.h>
int main(void){unsigned a,b,c,d;__cpuid_count(13,0,a,b,c,d);unsigned long long mask=((unsigned long long)d<<32)|a;
 puts("[");unsigned printed=0;for(unsigned i=2;i<63;i++)if(mask&(1ULL<<i)){__cpuid_count(13,i,a,b,c,d);if(printed++)puts(",");printf("{\\"type\\":%u,\\"size\\":%u,\\"offset\\":%u,\\"cpuid_ecx\\":%u}",i,a,b,c);}puts("]");}
''')
    binary = output/'cpuid'
    run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', source, '-o', binary], commands)
    return json.loads(run([binary], commands))


def note(owner, kind, payload):
    name = owner+b'\0'
    return (struct.pack('<III', len(name), len(payload), kind)+name+b'\0'*((-len(name))%4)
            +payload+b'\0'*((-len(payload))%4))


def add_layout(core, records):
    data = bytearray(core)
    wide = data[4] == 2
    layout = b''.join(struct.pack('<IIII', row['type'], row['size'], row['offset'], 0)
                      for row in records if not row['cpuid_ecx'] & 1)
    extra = note(b'LINUX', 0x205, layout)
    ph = 64 if wide else 52
    size_at = ph+(32 if wide else 16)
    memory_at = ph+(40 if wide else 20)
    fmt = '<Q' if wide else '<I'
    struct.pack_into(fmt, data, size_at, struct.unpack_from(fmt, data, size_at)[0]+len(extra))
    struct.pack_into(fmt, data, memory_at, struct.unpack_from(fmt, data, memory_at)[0]+len(extra))
    data.extend(extra)
    return bytes(data), layout


def decode_macho(data):
    wide = struct.unpack_from('<I', data)[0] == 0xfeedfacf
    position = 32 if wide else 28
    threads, archives, layouts = [], {}, []
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, position)
        if command == 4:
            cursor = position+8
            states = []
            while cursor < position+size:
                flavor, count = struct.unpack_from('<II', data, cursor)
                end = cursor+8+count*4
                if end > position+size:
                    raise ValueError('thread flavor exceeds command')
                states.append((flavor, data[cursor+8:end]))
                cursor = end
            threads.append(states)
        if command == 0x31:
            owner = data[position+8:position+24].rstrip(b'\0')
            offset, length = struct.unpack_from('<QQ', data, position+24)
            payload = data[offset:offset+length]
            if len(payload) != length:
                raise ValueError('archive outside Mach-O')
            if owner == b'DEARUST.XSTATE':
                version, elf_class, tid, kind, reserved, length = struct.unpack_from('<IIQIIQ', payload)
                if version != 1 or reserved or len(payload) != 32+length:
                    raise ValueError('invalid raw archive header')
                if (tid, kind) in archives:
                    raise ValueError('duplicate raw archive')
                archives[tid, kind] = (elf_class, payload[32:])
            elif owner == b'DEARUST.XLAYOUT':
                layouts.append(payload)
        position += size
    return threads, archives, layouts


def expected_state(raw, bits, abi, cpuid):
    enabled, active, compacted = (struct.unpack_from('<Q', raw, at)[0] for at in (464, 512, 520))
    if compacted:
        raise ValueError('capture is unexpectedly compacted')
    mode = 'avx512' if enabled & 0xe0 else 'avx' if enabled & 4 else 'float'
    result = bytearray(abi[f'{mode}{bits}_size'])
    base = lambda name: abi[f'base{bits}_{name}']
    def copy(destination, source, length):
        if source+length > len(raw):
            raise ValueError('oracle source exceeds captured state')
        result[destination:destination+length] = raw[source:source+length]
    copy(base('mxcsrmask'), 28, 4)
    if active & 1:
        copy(base('fcw'), 0, 2)
        copy(base('fsw'), 2, 2)
        copy(base('ftw'), 4, 1)
        copy(base('fop'), 6, 2)
        if bits == 64:
            copy(base('ip'), 8, 8)
            copy(base('dp'), 16, 8)
        else:
            copy(base('ip'), 8, 4)
            copy(base('cs'), 12, 2)
            copy(base('dp'), 16, 4)
            copy(base('ds'), 20, 2)
        for index in range(8):
            copy(base('stmm0')+16*index, 32+16*index, 10)
    else:
        struct.pack_into('<H', result, base('fcw'), 0x037f)
    if active & 6:
        copy(base('mxcsr'), 24, 4)
    else:
        struct.pack_into('<I', result, base('mxcsr'), 0x1f80)
    registers = 16 if bits == 64 else 8
    if active & 2:
        copy(base('xmm0'), 160, registers*16)
    offsets = {row['type']:row['offset'] for row in cpuid}
    if active & 4:
        copy(abi[f'avx{bits}_ymmh'], offsets[2], registers*16)
    if mode == 'avx512':
        if active & (1 << 5):
            copy(abi[f'avx512_{bits}_k0'], offsets[5], 64)
        if active & (1 << 6):
            copy(abi[f'avx512_{bits}_zmmh0'], offsets[6], registers*32)
        if bits == 64 and active & (1 << 7):
            copy(abi['avx512_64_zmm16'], offsets[7], 1024)
    return {'float':5 if bits == 64 else 2,'avx':17 if bits == 64 else 16,
            'avx512':20 if bits == 64 else 19}[mode], bytes(result)


def verify_capture(core, capture, bits, abi, cpuid, expected_layout):
    states, archives, layouts = decode_macho(core.read_bytes())
    status_files = sorted(capture.glob('thread-*.prstatus'))
    if len(states) != len(status_files):
        raise ValueError('lost captured thread')
    observed = []
    for index, status in enumerate(status_files):
        prstatus = status.read_bytes()
        tid = struct.unpack_from('<I', prstatus, 32 if bits == 64 else 24)[0]
        raw = status.with_suffix('.xstate').read_bytes()
        expected = expected_state(raw, bits, abi, cpuid)
        if len(states[index]) != 2 or states[index][1] != expected:
            actual = states[index][1] if len(states[index]) > 1 else (None, b'')
            differences = [i for i,(a,b) in enumerate(zip(actual[1],expected[1])) if a != b]
            raise ValueError(f'XNU state differs for tid{tid}: flavor{actual[0]}/{expected[0]}, offsets{differences[:16]}')
        sources = [(2, status.with_suffix('.fpreg')), (0x202, status.with_suffix('.xstate'))]
        if status.with_suffix('.fpx').exists():
            sources.append((0x46e62b7f, status.with_suffix('.fpx')))
        for kind, source in sources:
            if archives.pop((tid, kind), None) != (2 if bits == 64 else 1, source.read_bytes()):
                raise ValueError('raw source archive differs')
        # Known vectors loaded in assembly, independent of either decoder.
        marker = (0x21+index*0x31) if bits == 64 else 0x42
        xmm = expected[1][abi[f'base{bits}_xmm0']:abi[f'base{bits}_xmm0']+16]
        if xmm != bytes((marker+byte)&255 for byte in range(16)):
            raise ValueError('actual capture did not preserve assembly pattern')
        observed.append(dict(tid=tid, flavor=expected[0], bytes=len(expected[1]),
                             standard_sha256=hashlib.sha256(expected[1]).hexdigest()))
    if archives or layouts != ([] if expected_layout is None else [expected_layout]):
        raise ValueError('unexpected raw archives/layout notes')
    return observed


def read_with_lldb(debugger, destination, output, label, vector=True):
    """Record external-reader behavior without allowing system core artifacts."""
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = str(debugger.resolve().parents[1]/'lib64')
    temporary = output/(label+'-tmp')
    temporary.mkdir()
    env['TMPDIR'] = str(temporary)
    command = [str(debugger.resolve()), '--no-lldbinit', '--batch', '--core', str(destination),
               '-o', 'thread list', '-o', 'register read']
    if vector:
        command += ['-o', 'register read xmm0']
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
    return dict(case=label, command=command, returncode=result.returncode,
                stdout=result.stdout, stderr=result.stderr,
                scope='External-reader behavior; the independent XNU oracle checks FP/vector bytes.')


def strip_reader_extensions(data, general_only):
    """Preserve file offsets while removing raw notes/optional FP thread flavors."""
    data = bytearray(data)
    header = 32 if struct.unpack_from('<I', data)[0] == 0xfeedfacf else 28
    commands = []
    cursor = header
    old_size = struct.unpack_from('<I', data, 20)[0]
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, cursor)
        command = data[cursor:cursor+size]
        if kind != 0x31:
            if kind == 4 and general_only:
                count = struct.unpack_from('<I', command, 12)[0]
                command = command[:16+count*4]
                struct.pack_into('<I', command, 4, len(command))
            commands.append(command)
        cursor += size
    replacement = b''.join(commands)
    data[header:header+old_size] = replacement+b'\0'*(old_size-len(replacement))
    struct.pack_into('<II', data, 16, len(commands), len(replacement))
    return data


def reader_isolation(args, core, current, output, commands):
    """Compare the same captured i386 input across both baselines and new output."""
    cases = [('current', current)]
    for label, converter in [('original-c', args.reader_original), ('prior-rust', args.reader_prior)]:
        destination = output/(label+'.macho')
        run([converter.resolve(strict=True), core, destination], commands)
        cases.append((label, destination))
    for label, general_only in [('current-no-raw-notes', False), ('current-gpr-only', True)]:
        destination = output/(label+'.macho')
        destination.write_bytes(strip_reader_extensions(current.read_bytes(), general_only))
        cases.append((label, destination))
    return [read_with_lldb(args.lldb, destination, output, 'isolation-'+label, vector=False)
            for label, destination in cases]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'source', 'build', 'converter'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--lldb', type=Path)
    parser.add_argument('--reader-original', type=Path,
                        help='With --reader-prior and --lldb, reproduce the five-way i386 reader comparison.')
    parser.add_argument('--reader-prior', type=Path)
    args = parser.parse_args()
    if (args.reader_original is None) != (args.reader_prior is None) or (args.reader_original and not args.lldb):
        parser.error('reader isolation requires --reader-original, --reader-prior and --lldb together')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    output = args.output.resolve()
    source, build, converter = (getattr(args, name).resolve(strict=True) for name in ('source','build','converter'))
    if any(output.is_relative_to(path) or path.is_relative_to(output) for path in (source,build)):
        parser.error('output must be separate from reference trees')
    output.mkdir(parents=True, exist_ok=False)
    commands, results, readers = [], {}, []
    fixture = Path(__file__).resolve().parent/'fixtures/core-register-state.c'
    child_source = fixture.with_name('core-register-state32.S')
    native = output/'capture-registers'
    run(['clang','-O2','-Wall','-Wextra','-Werror','-pthread',fixture,'-o',native],commands)
    child = output/'register-child32'
    run(['clang','-target','i386-linux-gnu','-c',child_source,'-o',output/'child32.o'],commands)
    run(['ld.lld','-m','elf_i386','-nostdlib','-o',child,output/'child32.o'],commands)
    abi = sdk_oracle(source,build,output,commands)
    cpuid = capture_layout(output,commands)
    for bits in (64,32):
        capture = output/f'capture{bits}'
        run([native,capture]+([] if bits == 64 else [child]),commands)
        original = capture/'registers.elf'
        with_layout, layout = add_layout(original.read_bytes(),cpuid)
        augmented = capture/'registers-with-layout.elf'
        augmented.write_bytes(with_layout)
        for name, core, metadata in [('kernel-regsets',original,None),('captured-cpuid-layout',augmented,layout)]:
            destination = capture/(name+'.macho')
            run([converter,core,destination],commands)
            results[f'{bits}-{name}'] = verify_capture(destination,capture,bits,abi,cpuid,metadata)
            if args.lldb:
                readers.append(read_with_lldb(args.lldb, destination, output, f'{bits}-{name}'))
            if bits == 32 and metadata is None and args.reader_original:
                readers.extend(reader_isolation(args, original, destination, output, commands))
    files = [Path(__file__).resolve(),fixture,child_source,converter,
             *[p for p in output.rglob('*') if p.is_file()]]
    if args.reader_original:
        files += [args.reader_original.resolve(strict=True), args.reader_prior.resolve(strict=True)]
    if args.lldb:
        files.append(args.lldb.resolve(strict=True))
    report = dict(scope=__doc__,commands=commands,xnu_sdk_abi=abi,source_cpu_cpuid=cpuid,
                  results=results,debugger=readers,sha256={str(path):sha(path) for path in files})
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(results,indent=2))


if __name__ == '__main__':
    main()
