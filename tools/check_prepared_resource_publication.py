#!/usr/bin/env python3
"""Check exact owned publication methods with native allocation/epoll failures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import patch_prepared_resource_publication as publication


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True, help='frozen owned server with transactional add/removeMonitor')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    fixture = root / 'fixtures/prepared-resource-publication-native.cpp.in'
    server_path = args.candidate.resolve(strict=True) / 'src/server.cpp'
    original_server_path = source / 'src/server.cpp'
    monitor_path = source / 'internal-include/darlingserver/monitor.hpp'
    paths = [fixture, server_path, original_server_path, monitor_path,
             Path(publication.__file__), Path(__file__).resolve()]
    inputs = {str(path): digest(path) for path in paths}
    include = output / 'include/darlingserver'
    include.mkdir(parents=True)
    (include / 'monitor.hpp').write_bytes(monitor_path.read_bytes())
    # Only descriptor ownership is adapted. The Monitor declaration, constructor,
    # publication methods and add/remove operations are exact transformed code.
    (include / 'utility.hpp').write_text('''#pragma once
#include <unistd.h>
namespace DarlingServer { class FD {
    int _fd;
public:
    explicit FD(int fd): _fd(fd) {}
    ~FD() { if (_fd >= 0) close(_fd); }
    int fd() const { return _fd; }
    void release() { _fd = -1; }
}; }
''')
    server = server_path.read_text()
    begin = server.index('void DarlingServer::Server::addMonitor(')
    end = server.index('DarlingServer::Monitor::Monitor(', begin)
    original_server = original_server_path.read_text()
    begin_constructor = original_server.index('DarlingServer::Monitor::Monitor(')
    end_constructor = original_server.index('void DarlingServer::Monitor::enable(', begin_constructor)
    text = fixture.read_text()
    for name in ('PROCESS_DECLARATIONS', 'THREAD_DECLARATIONS', 'MONITOR_DECLARATION',
                 'PROCESS_METHODS', 'THREAD_METHODS', 'THREAD_DESTRUCTOR', 'MONITOR_METHODS'):
        text = text.replace('// ' + name, getattr(publication, name))
    text = text.replace('// ADD_REMOVE_MONITOR_METHODS', server[begin:end])
    text = text.replace('// MONITOR_CONSTRUCTOR', original_server[begin_constructor:end_constructor])
    selected = output / 'prepared-resource-publication-native.cpp'
    selected.write_text(text)
    records = []
    for variant in (('native', 'sanitized') if args.sanitizers else ('native',)):
        binary = output / variant
        flags = ['-O2'] if variant == 'native' else ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        commands = [['clang++', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror', '-Wno-reorder-ctor',
                     '-pthread', '-I' + str(include.parent), str(selected), '-o', str(binary)], [str(binary)]]
        for command in commands:
            env = os.environ.copy()
            env['ASAN_OPTIONS'] = 'detect_leaks=1:alloc_dealloc_mismatch=1'
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
            records.append(dict(variant=variant, command=command, exit_code=result.returncode,
                                stdout=result.stdout, stderr=result.stderr))
            if result.returncode:
                break
        if records[-1]['exit_code']:
            break
    unchanged = all(digest(Path(name)) == expected for name, expected in inputs.items())
    report = dict(passed=unchanged and all(item['exit_code'] == 0 for item in records),
                  input_sha256=inputs, owned_source_sha256=digest(selected),
                  inputs_unchanged=unchanged, commands=records,
                  scope='exact monitor/attachment publication methods with adapted surrounding state; no dtape handle, RPC, guest or resource-enforcement claim')
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
