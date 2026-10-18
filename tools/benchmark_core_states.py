#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Benchmark exact C, prior Rust and current core converters on captured x86 state.

Requires --capture-root from tools/check_core_states.py (capture32 and capture64).
All fixtures, frozen executable copies, output and reports go into a fresh
--output directory; references, installed runtime and global caches are untouched.
Wall = native C fork/exec/wait fresh CLI; CPU/RSS = wait4 converter child.
Cold = POSIX_FADV_DONTNEED only on each owned, fsynced mapped input before sample.
No output fsync: cached converter writes, not storage durability. Small --only
startup64 --rounds 1 --cold-rounds 0 provides a CLI smoke check.

Full FP/FPX/XSAVE raw archives are validated in deterministic FP, FPX, XSAVE order.
This script checks byte preservation and scales real register images, not whether
an external debugger exposes AVX/AVX512 or supports a particular Mach-O class.
"""
import argparse
import hashlib
import itertools
import json
import os
import pathlib
import shutil
import statistics
import struct
import subprocess
import time
# Initialized only after command-line validation; every mutation stays in P.
P = None
CAPTURE_ROOT = None
REPO = pathlib.Path(__file__).resolve().parents[1]
BIN = {}
FP_KINDS = (2, 0x46e62b7f, 0x202)

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for x in iter(lambda: f.read(0x100000), b''):
            h.update(x)
    return h.hexdigest()

def pad(x):
    return x + b'\x00' * (-len(x) % 4)

def note(owner, kind, data):
    return struct.pack('<III', len(owner), len(data), kind) + pad(owner) + pad(data)

def captured(bits):
    data = (CAPTURE_ROOT / f'capture{bits}' / 'registers.elf').read_bytes()
    wide = bits == 64
    if data[:7] != b'\x7fELF' + bytes([2 if wide else 1, 1, 1]):
        raise ValueError('capture must be little-endian ELF of the requested class')
    phoff = struct.unpack_from('<Q' if wide else '<I', data, 32 if wide else 28)[0]
    entsize, count = struct.unpack_from('<HH', data, 54 if wide else 42)
    groups = []
    for index in range(count):
        ph = phoff + index * entsize
        if struct.unpack_from('<I', data, ph)[0] != 4:
            continue
        offset = struct.unpack_from('<Q' if wide else '<I', data, ph + (8 if wide else 4))[0]
        length = struct.unpack_from('<Q' if wide else '<I', data, ph + (32 if wide else 16))[0]
        end = offset + length
        if end > len(data):
            raise ValueError('truncated captured PT_NOTE')
        while offset < end:
            namesz, size, kind = struct.unpack_from('<III', data, offset)
            offset += 12
            owner = data[offset:offset + namesz]
            offset += namesz + 3 & ~3
            payload = data[offset:offset + size]
            offset += size + 3 & ~3
            if offset > end or len(payload) != size:
                raise ValueError('truncated captured note')
            if kind == 1:
                groups.append([])
            if kind == 1 or kind in FP_KINDS:
                if not groups:
                    raise ValueError('orphan captured floating note')
                groups[-1].append((owner, kind, payload))
    if not groups:
        raise ValueError('capture has no threads')
    return groups

def expected_raw(group, tid):
    """Raw output order is format precedence, independent of source note order."""
    values = {kind: payload for _, kind, payload in group if kind in FP_KINDS}
    return [
        (tid, kind, len(values[kind]), hashlib.sha256(values[kind]).hexdigest())
        for kind in FP_KINDS if kind in values
    ]

def fixture(name, bits, n, full, mapped=0, zero=0):
    groups = captured(bits)
    records = []
    expected = []
    for i in range(n):
        for owner, k, d in groups[i % len(groups)]:
            if k != 1 and (not full):
                continue
            if k == 1:
                d = bytearray(d)
                struct.pack_into('<I', d, 32 if bits == 64 else 24, 10000 + i)
                d = bytes(d)
            records.append(note(owner, k, d))
        if full:
            expected.extend(expected_raw(groups[i % len(groups)], 10000 + i))
    records.append(note(b'CORE\x00', 0x46494c45, struct.pack('<QQ' if bits == 64 else '<II', 0, 4096)))
    notes = b''.join(records)
    wide = bits == 64
    hs = 64 if wide else 52
    ps = 56 if wide else 32
    pc = 1 + bool(mapped or zero)
    noff = hs + ps * pc
    doff = noff + len(notes) + 4095 & ~4095
    ident = b'\x7fELF' + bytes([2 if wide else 1, 1, 1, 0]) + bytes(8)
    if wide:
        hdr = ident + struct.pack('<HHIQQQIHHHHHH', 4, 62, 1, 0, hs, 0, 0, hs, ps, pc, 0, 0, 0)
        ph = struct.pack('<IIQQQQQQ', 4, 0, noff, 0, 0, len(notes), 0, 4)
        if pc == 2:
            ph += struct.pack('<IIQQQQQQ', 1, 6, doff, 0x100000, 0, mapped, mapped + zero, 4096)
    else:
        hdr = ident + struct.pack('<HHIIIIIHHHHHH', 4, 3, 1, 0, hs, 0, 0, hs, ps, pc, 0, 0, 0)
        ph = struct.pack('<IIIIIIII', 4, noff, 0, 0, len(notes), 0, 0, 4)
        if pc == 2:
            ph += struct.pack('<IIIIIIII', 1, doff, 0x100000, 0, mapped, mapped + zero, 6, 4096)
    path = P / (name + '.elf')
    with path.open('wb') as f:
        f.write(hdr + ph + notes)
        if mapped or zero:
            f.write(bytes(doff - f.tell()))
            chunk = bytes(range(256)) * 256
            for j in range(0, mapped, len(chunk)):
                f.write(chunk[:min(len(chunk), mapped - j)])
        f.flush()
        os.fsync(f.fileno())
    return {'name': name, 'bits': bits, 'threads': n, 'full_fp': full, 'mapped_bytes': mapped, 'zero_bytes': zero, 'path': str(path), 'input_bytes': path.stat().st_size, 'input_sha256': sha(path), 'raw_expected': expected}

def actual_fixture(bits):
    source = CAPTURE_ROOT / f'capture{bits}/registers.elf'
    name = f'actual_capture{bits}'
    path = P / (name + '.elf')
    shutil.copyfile(source, path)
    with path.open('rb') as f:
        os.fsync(f.fileno())
    groups = captured(bits)
    expected = []
    for group in groups:
        tid = struct.unpack_from('<I', group[0][2], 32 if bits == 64 else 24)[0]
        expected.extend(expected_raw(group, tid))
    return {'name': name, 'bits': bits, 'threads': len(groups), 'full_fp': True, 'mapped_bytes': 0, 'zero_bytes': 0, 'path': str(path), 'source_capture': str(source), 'input_bytes': path.stat().st_size, 'input_sha256': sha(path), 'raw_expected': expected}

def parse_output(path):
    b = path.read_bytes()
    magic = struct.unpack_from('<I', b)[0]
    wide = magic == 0xfeedfacf
    off = 32 if wide else 28
    n = struct.unpack_from('<I', b, 16)[0]
    regs = []
    floats = []
    segments = []
    raw = []
    for _ in range(n):
        kind, size = struct.unpack_from('<II', b, off)
        if kind == 4:
            at = off + 8
            while at < off + size:
                fl, count = struct.unpack_from('<II', b, at)
                payload = b[at + 8:at + 8 + 4 * count]
                at += 8 + 4 * count
                if fl == (4 if wide else 1):
                    regs.append(hashlib.sha256(payload).hexdigest())
                else:
                    floats.append({'flavor': fl, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), 'all_zero': not any(payload)})
        elif kind in (1, 25):
            fmt = '<QQQQ' if kind == 25 else '<IIII'
            addr, mem, fo, fs = struct.unpack_from(fmt, b, off + 24)
            segments.append((addr, mem, fs, hashlib.sha256(b[fo:fo + fs]).hexdigest()))
        elif kind == 49:
            owner = b[off + 8:off + 24].rstrip(b'\x00')
            fo, fs = struct.unpack_from('<QQ', b, off + 24)
            payload = b[fo:fo + fs]
            if owner == b'DEARUST.XSTATE':
                version, cl, tid, k, res, sz = struct.unpack_from('<IIQIIQ', payload)
                assert version == 1 and res == 0 and (len(payload) == 32 + sz)
                raw.append((tid, k, sz, hashlib.sha256(payload[32:]).hexdigest()))
        off += size
    return {'bytes': len(b), 'gpr': regs, 'fp': floats, 'segments': segments, 'raw': raw}

def run(binary, inp, out, cold=False):
    if cold:
        with open(inp, 'rb') as f:
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    p = subprocess.run([str(P / 'launcher'), str(binary), str(inp), str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    r = json.loads(p.stdout)
    r['output_bytes'] = out.stat().st_size
    return r

def distribution(a):
    a = sorted(a)
    q = lambda p: a[round((len(a) - 1) * p)]
    return {'n': len(a), 'min': a[0], 'q1': q(0.25), 'median': statistics.median(a), 'q3': q(0.75), 'p90': q(0.9), 'p99': q(0.99), 'max': a[-1]}

def main():
    global P, CAPTURE_ROOT, BIN
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'original', 'prior', 'current', 'capture-root'):
        ap.add_argument('--' + name, type=pathlib.Path, required=True)
    ap.add_argument('--cc', default='cc', help='C compiler executable for the tiny native launcher')
    ap.add_argument('--rounds', type=int, default=24)
    ap.add_argument('--cold-rounds', type=int, default=12)
    ap.add_argument('--only', default='', help='Comma-separated generated scenario names (default: all and actual captures)')
    args = ap.parse_args()
    if args.rounds < 1 or args.cold_rounds < 0:
        ap.error('rounds must be positive and cold-rounds nonnegative')
    if not hasattr(os, 'sched_getaffinity') or not hasattr(os, 'posix_fadvise'):
        ap.error('Linux affinity and POSIX fadvise APIs are required')
    CAPTURE_ROOT = args.capture_root.resolve(strict=True)
    P = args.output.resolve()
    if P.is_relative_to(CAPTURE_ROOT) or CAPTURE_ROOT.is_relative_to(P):
        ap.error('output must be separate from the capture tree')
    supplied = {k: getattr(args, k.removesuffix('_c').removesuffix('_rust')).resolve(strict=True) for k in ('original_c', 'prior_rust', 'current_rust')}
    if any((path.is_relative_to(P) for path in supplied.values())):
        ap.error('output must be separate from converter inputs')
    P.mkdir(parents=True, exist_ok=False)
    for key, path in supplied.items():
        BIN[key] = P / key
        shutil.copy2(path, BIN[key])
    launcher_source = pathlib.Path(__file__).resolve().parent / 'fixtures/core-benchmark-launcher.c'
    shutil.copy2(launcher_source, P / 'launcher.c')
    compile_command = [args.cc, '-O2', '-Wall', '-Wextra', '-Werror', str(P / 'launcher.c'), '-o', str(P / 'launcher')]
    subprocess.run(compile_command, check=True, capture_output=True)
    allowed = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {allowed[0]})
    specs = [
        ('startup64', 64, 1, False, 0, 0),
        ('startup32', 32, 1, False, 0, 0),
        ('full32', 32, 1, True, 0, 0),
        ('full64', 64, 3, True, 0, 0),
        ('full64_16', 64, 16, True, 0, 0),
        ('full64_256', 64, 256, True, 0, 0),
        ('nofp64_1024', 64, 1024, False, 0, 0),
        ('mapped64_16_64MiB', 64, 16, True, 64 * 1024 ** 2, 0),
        ('zerofill64_64MiB', 64, 1, False, 0, 64 * 1024 ** 2),
    ]
    specs = [s for s in specs if not args.only or s[0] in args.only.split(',')]
    if args.only and (not specs):
        ap.error('unknown --only scenario')
    fixtures = [fixture(*s) for s in specs]
    fixtures += [actual_fixture(bits) for bits in [32, 64]] if not args.only else []
    sources = {str(p.relative_to(REPO)): sha(p) for p in (REPO / 'crates/dearust-coredump/src/lib.rs', REPO / 'crates/dearust-coredump/src/fp.rs', REPO / 'Cargo.lock') if p.exists()}
    report = {
        'schema': 1,
        'method': __doc__,
        'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'uname': list(os.uname()),
        'affinity_cpu': allowed[0],
        'available_cpus': allowed,
        'sources': sources,
        'binaries': {
            k: {'path': str(v), 'sha256': sha(v), 'bytes': v.stat().st_size}
            for k, v in BIN.items()
        },
        'launcher_source_sha256': sha(P / 'launcher.c'),
        'launcher_sha256': sha(P / 'launcher'),
        'script_sha256': sha(pathlib.Path(__file__)),
        'supplied_binaries': {k: str(v) for k, v in supplied.items()},
        'compile_command': compile_command,
        'fixtures': [],
    }
    perms = list(itertools.permutations(BIN))
    samples = []
    for si, s in enumerate(fixtures):
        print('MEASURE', s['name'], flush=True)
        checks = {}
        first = {}
        for k, binary in BIN.items():
            out = P / (k + '.macho')
            first[k] = run(binary, s['path'], out)
            checks[k] = parse_output(out)
        for k in BIN:
            assert checks[k]['gpr'] == checks['original_c']['gpr']
            assert checks[k]['segments'] == checks['original_c']['segments']
        assert checks['current_rust']['raw'] == s['raw_expected'], (s['name'], 'raw-mismatch')
        assert len(checks['current_rust']['fp']) == (s['threads'] if s['full_fp'] else 0)
        for k in BIN:
            for _ in range(2):
                run(BIN[k], s['path'], P / (k + '.macho'))
        for mode, rounds in [('warm', args.rounds), ('owned_input_fadvise_cold', args.cold_rounds if s['mapped_bytes'] else 0)]:
            for ri in range(rounds):
                order = perms[(ri + si) % len(perms)]
                for pos, k in enumerate(order):
                    sample = run(BIN[k], s['path'], P / (k + '.macho'), mode != 'warm')
                    samples.append(dict(scenario=s['name'], mode=mode, round=ri, position=pos, variant=k, **sample))
        clean = {k: v for k, v in s.items() if k != 'raw_expected'}
        clean['first_fresh_process_warm_filesystem'] = first
        clean['validation'] = {k: {'bytes': v['bytes'], 'gpr_threads': len(v['gpr']), 'derived_fp_threads': len(v['fp']), 'raw_notes': len(v['raw']), 'segments': v['segments']} for k, v in checks.items()}
        report['fixtures'].append(clean)
        report['samples'] = samples
        (P / 'benchmark-report.json').write_text(json.dumps(report, indent=2) + '\n')
    summary = []
    for s in fixtures:
        for mode in ['warm', 'owned_input_fadvise_cold']:
            row = {'scenario': s['name'], 'mode': mode, 'variants': {}}
            for k in BIN:
                a = [x for x in samples if x['scenario'] == s['name'] and x['mode'] == mode and (x['variant'] == k)]
                if a:
                    row['variants'][k] = {key: distribution([x[key] for x in a]) for key in ['wall_us', 'cpu_us', 'maxrss_kib', 'minor_faults', 'major_faults', 'output_bytes']}
            if row['variants']:
                for old in ['original_c', 'prior_rust']:
                    for metric in ['cpu_us', 'wall_us', 'maxrss_kib']:
                        denom = row['variants'][old][metric]['median']
                        row[f'current_vs_{old}_{metric}_percent'] = (row['variants']['current_rust'][metric]['median'] / denom - 1) * 100 if denom else None
                summary.append(row)
    report['summary'] = summary
    report['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    (P / 'benchmark-report.json').write_text(json.dumps(report, indent=2) + '\n')
    for row in summary:
        print(row['scenario'], row['mode'], json.dumps({k: {m: round(v[m]['median'], 3) for m in ['cpu_us', 'wall_us', 'maxrss_kib', 'output_bytes']} for k, v in row['variants'].items()}), flush=True)
if __name__ == '__main__':
    main()
