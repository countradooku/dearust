#!/usr/bin/env python3
"""Check exact prepared-checkin boundary and owned nonblocking error slot flow."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from patch_prepared_resource_checkin import patch_checkin


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True, type=Path, help='owned server before checkin transform')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    server = args.server.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    fixture = root / 'fixtures/prepared-resource-checkin-native.cpp.in'
    protocol = server / 'include/darlingserver/rpc.h'
    paths = {name: server / ('internal-include/darlingserver' if name.endswith('.hpp') else 'src') / name
             for name in ('server.hpp', 'server.cpp', 'call.cpp')}
    inputs = {str(path): digest(path) for path in [*paths.values(), protocol, fixture,
              root / 'patch_prepared_resource_checkin.py', Path(__file__).resolve()]}
    transformed = patch_checkin({name: path.read_text() for name, path in paths.items()})
    call = transformed['call.cpp']
    begin = call.index('    const bool preparedIsCheckin =')
    end = call.index('\n\tif (callLog.', begin)
    boundary = call[begin:end]
    selected_server = transformed['server.cpp']
    begin = selected_server.index('void DarlingServer::Server::queuePreparedCheckinFailure(')
    methods = selected_server[begin:]
    begin = selected_server.index('\t\tif (!_preparedCheckinFailurePending && (_canRead || !_inbox.empty())) {')
    end = selected_server.index('\n\t\t// reset the eventfd', begin)
    inbox = selected_server[begin:end]
    continuation = 'if (!_preparedCheckinFailurePending && !_inbox.empty()) continue;'
    if selected_server.count(continuation) != 1:
        raise ValueError('buffered-inbox continuation is not unique')
    header = transformed['server.hpp']
    begin = header.index('        // Constructed during server startup,')
    end = header.index('\t\tvoid _worker(', begin)
    fields = header[begin:end]
    rendered = fixture.read_text()
    for marker, value in [('@PROTOCOL@', json.dumps(str(protocol))), ('// SERVER_FIELDS', fields),
                          ('// SERVER_METHODS', methods), ('// MANAGED_BOUNDARY', boundary),
                          ('// INBOX_DISPATCH', inbox),
                          ('// BUFFERED_CONTINUATION', continuation.replace('continue;', 'return true;'))]:
        rendered = rendered.replace(marker, value)
    selected = output / 'prepared-resource-checkin-native.cpp'
    selected.write_text(rendered)
    records = []
    for variant in (('native', 'sanitized') if args.sanitizers else ('native',)):
        binary = output / variant
        flags = ['-O2'] if variant == 'native' else ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        for command in [['clang++', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror', '-pthread', str(selected), '-o', str(binary)], [str(binary)]]:
            env = os.environ.copy()
            env['ASAN_OPTIONS'] = 'detect_leaks=1:alloc_dealloc_mismatch=1'
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
            records.append(dict(variant=variant, command=command, exit_code=result.returncode,
                                stdout=result.stdout, stderr=result.stderr))
            if result.returncode:
                break
        if records[-1]['exit_code']:
            break
    unchanged = all(digest(Path(path)) == value for path, value in inputs.items())
    report = dict(passed=unchanged and all(item['exit_code'] == 0 for item in records),
                  input_sha256=inputs, owned_source_sha256=digest(selected), inputs_unchanged=unchanged,
                  commands=records, scope='exact registration boundary/error-slot/inbox dispatch with adapted identity/registration/Message state and ordinary native datagram replies; no adversarial RPC, live sender authentication, dtape or guest claim')
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
