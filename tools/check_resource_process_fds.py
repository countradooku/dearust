#!/usr/bin/env python3
"""Test focused Process descriptor ownership with native allocation injection.

Uses exact original FD utility code and the transformed candidate constructor
prefix. Duct-tape allocations, factory publication, eternal identity and full
runtime OOM lifecycle are outside this check. No RPC or guest process runs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from patch_resource_process_fds import (patch_call_source, patch_process_header,
                                        patch_process_source, patch_state)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path, help='owned resource server build directory')
    parser.add_argument('--source', required=True, type=Path, help='original darlingserver source directory for exact FD utility')
    parser.add_argument('--state', required=True, type=Path, help='candidate resource-server-state.inc')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    source = args.source.resolve(strict=True)
    state = args.state.resolve(strict=True)
    output = args.output.resolve()
    root = Path(__file__).resolve().parent
    for reference in (candidate, source, root):
        if output.is_relative_to(reference) or reference.is_relative_to(output):
            parser.error('output must be disjoint from candidate, source and tools')
    if output == state or state.is_relative_to(output):
        parser.error('output must not contain state source')
    fixture = root / 'fixtures/resource-process-fds-native.cpp.in'
    header = candidate / 'internal-include/darlingserver/process.hpp'
    process = candidate / 'src/process.cpp'
    call = candidate / 'src/call.cpp'
    utility_header = source / 'internal-include/darlingserver/utility.hpp'
    utility_source = source / 'src/utility.cpp'
    inputs = [Path(__file__).resolve(), root / 'patch_resource_process_fds.py', fixture,
              header, process, call, state, utility_header, utility_source]
    frozen = {p: p.read_bytes() for p in inputs}
    hashes = {str(p): hashlib.sha256(data).hexdigest() for p, data in frozen.items()}
    patched_header = patch_process_header(frozen[header].decode())
    patched_process = patch_process_source(frozen[process].decode())
    patched_call = patch_call_source(frozen[call].decode())
    patched_state = patch_state(frozen[state].decode())
    declaration = re.findall(r'Process\(ID id, NSID nsid, Architecture architecture, FD pipe = FD\(\)\);', patched_header)
    if len(declaration) != 1:
        raise ValueError('expected one transformed Process constructor declaration')
    begin = patched_process.index('DarlingServer::Process::Process(ID id,')
    end = patched_process.index('\n\t// we could use stat', begin)
    prefix = patched_process[begin:end] + '\n}\n'
    state_begin = patched_state.index('FD candidateOwner(syscall(SYS_pidfd_open, candidate, 0));')
    state_end = patched_state.index('\n            FD candidateDirectory(', state_begin)
    fragment = patched_state[state_begin:state_end]
    text = frozen[fixture].decode()
    for marker, value in [('@PROCESS_DECLARATION@', declaration[0]), ('@PROCESS_PREFIX@', prefix), ('@STATE_FRAGMENT@', fragment)]:
        if text.count(marker) != 1:
            raise ValueError('fixture marker not unique: ' + marker)
        text = text.replace(marker, value)
    # Preserve the original Process source license alongside its extracted prefix.
    license_text = patched_process[:patched_process.index('#include')]
    output.mkdir(parents=True, exist_ok=False)
    include = output / 'include/darlingserver'
    include.mkdir(parents=True)
    (include / 'utility.hpp').write_bytes(frozen[utility_header])
    utility = output / 'utility.cpp'
    utility.write_bytes(frozen[utility_source])
    translation = output / 'resource-process-fds-native.cpp'
    translation.write_text(license_text + text)
    generated = [translation, utility, include / 'utility.hpp']
    for name, content in [('process.hpp', patched_header), ('process.cpp', patched_process),
                          ('call.cpp', patched_call), ('resource-server-state.inc', patched_state)]:
        owned = output / name
        owned.write_text(content)
        generated.append(owned)
    records = []
    variants = ('native', 'sanitized') if args.sanitizers else ('native',)
    for variant in variants:
        flags = ['-O2'] if variant == 'native' else ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        binary = output / variant
        command = ['clang++', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror', '-I'+str(include.parent), '-Wl,--wrap=close', str(translation), str(utility), '-o', str(binary)]
        for phase, selected in [('compile', command), ('run', [str(binary)])]:
            env = os.environ.copy()
            env['ASAN_OPTIONS'] = 'detect_leaks=1'
            result = subprocess.run(selected, capture_output=True, text=True, env=env, timeout=60)
            records.append(dict(variant=variant, phase=phase, command=selected,
                                exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
            if result.returncode:
                break
        if records[-1]['exit_code']:
            break
    unchanged = all(digest(Path(name)) == value for name, value in hashes.items())
    passed = unchanged and len(records) == 2 * len(variants) and all(r['exit_code'] == 0 for r in records)
    report = dict(passed=passed, source_sha256=hashes, generated_sha256={str(p): digest(p) for p in generated},
                  commands=records, inputs_unchanged=unchanged,
                  scope='exact transformed Process FD prefix and fork-candidate acquisition; exact original utility; native nonallocating namespace-validator adapter; no dtape, EID/publication OOM, RPC, guest or old-kernel claim')
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('passed', 'inputs_unchanged', 'scope')}, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
