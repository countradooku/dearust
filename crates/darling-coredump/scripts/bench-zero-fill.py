#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare CLI zero-fill throughput on a synthetic 64 MiB anonymous mapping.
Usage: bench-zero-fill.py UPSTREAM_BINARY RUST_RELEASE_BINARY
Output goes to a temporary regular file (warm cache, no fsync). This measures
whole CLI zero-fill conversion; it does not establish real-core performance.
"""
import hashlib
import json
import os
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import tempfile
import time


def fixture():
    notes = struct.pack('<III', 5, 16, 0x46494C45) + b'CORE\0\0\0\0' + struct.pack('<QQ', 0, 4096)
    header = bytearray(64)
    header[:7] = b'\x7fELF\x02\x01\x01'
    struct.pack_into('<HHI', header, 16, 4, 62, 1)
    struct.pack_into('<Q', header, 32, 64)
    struct.pack_into('<HHH', header, 52, 64, 56, 2)
    phdrs = struct.pack('<IIQQQQQQ', 4, 0, 176, 0, 0, len(notes), len(notes), 4)
    phdrs += struct.pack('<IIQQQQQQ', 1, 6, 0, 0x1000, 0, 0, 64 * 1024 * 1024, 4096)
    return header + phdrs + notes


with tempfile.TemporaryDirectory(prefix='darling-core-bench-') as directory:
    core = Path(directory) / 'core'
    core.write_bytes(fixture())
    output = Path(directory) / 'converted'
    samples = {'upstream': [], 'rust': []}
    binaries = dict(zip(samples, map(os.path.abspath, sys.argv[1:3])))
    for iteration in range(23):
        # Alternate order to reduce systematic order bias; discard two warmups.
        for name in list(samples)[::1 if iteration % 2 == 0 else -1]:
            start = time.perf_counter_ns()
            subprocess.run([binaries[name], str(core), str(output)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elapsed = (time.perf_counter_ns() - start) / 1e6
            if iteration >= 2:
                samples[name].append(elapsed)
    medians = {name: statistics.median(values) for name, values in samples.items()}
    validation = {}
    for name, binary in binaries.items():
        usage = Path(directory) / 'usage'
        subprocess.run(['/usr/bin/time', '-f', '%M', '-o', str(usage), binary, str(core), str(output)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        digest = hashlib.sha256()
        with output.open('rb') as result:
            header = bytearray(result.read(104))
            # Upstream leaves segname bytes 1..15 uninitialized; normalize only those.
            header[41:56] = bytes(15)
            digest.update(header)
            while chunk := result.read(1024*1024):
                digest.update(chunk)
        validation[name] = {'output_bytes': output.stat().st_size, 'normalized_sha256': digest.hexdigest(), 'peak_rss_kib': int(usage.read_text())}
    assert validation['upstream']['normalized_sha256'] == validation['rust']['normalized_sha256']
    print(json.dumps({'scenario': 'synthetic 64 MiB zero mapping, temporary regular file, warm cache, no fsync', 'samples_ms': samples,
                      'median_ms': medians, 'median_speedup': medians['upstream'] / medians['rust'], 'validation': validation}, indent=2))
