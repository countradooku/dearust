#!/usr/bin/env python3
"""Native valid-protocol Process exit/kq semantics using exact owned methods."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from patch_prepared_resource_process_notifications import patch_process_notifications


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='reference darlingserver source, read only')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sanitizers', action='store_true')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    paths = {name: source / ('internal-include/darlingserver' if name.endswith('.hpp') else 'src') / name
             for name in ['process.hpp', 'process.cpp', 'kqchan.hpp', 'kqchan.cpp']}
    fixture = root / 'fixtures/prepared-resource-process-notifications-native.cpp.in'
    core = root / 'prepared-resource-process-notifications.inc'
    supplement = source / 'include/darlingserver/rpc-supplement.h'
    inputs = {str(path): digest(path) for path in [*paths.values(), fixture, core, supplement,
              root / 'patch_prepared_resource_process_notifications.py', Path(__file__).resolve()]}
    selected = patch_process_notifications({name: path.read_text() for name, path in paths.items()})
    ks = selected['kqchan.cpp']; ps = selected['process.cpp']; kh = selected['kqchan.hpp']
    fields = function(kh, '        // A Process can exit only once.', '\t\tstd::weak_ptr<DarlingServer::Process> _targetProcess;')
    fields = fields.replace('private:', 'public:')
    substitutions = {
        '@SUPPLEMENT@': json.dumps(str(supplement)),
        '// KQ_FIELDS': fields,
        '// CORE_METHODS': core.read_text(),
        '// BASE_DESTRUCTOR': function(ks, 'DarlingServer::Kqchan::~Kqchan()', 'uintptr_t DarlingServer::Kqchan::_idForProcess()'),
        '// KQ_DESTRUCTOR': function(ks, 'DarlingServer::Kqchan::Process::~Process()', 'uintptr_t DarlingServer::Kqchan::Process::_idForProcess()'),
        '// REGISTER_LISTENER': function(ps, 'void DarlingServer::Process::registerListeningKqchan(', 'void DarlingServer::Process::unregisterListeningKqchan('),
        '// PROCESS_METHODS': ps[ps.index('void DarlingServer::Process::notifyPreparedExitListeners() noexcept'):],
    }
    rendered = fixture.read_text()
    for key, value in substitutions.items():
        rendered = rendered.replace(key, value)
    translation = output / 'prepared-resource-process-notifications-native.cpp'
    translation.write_text(rendered)
    records = []
    for variant in (('native', 'sanitized') if args.sanitizers else ('native',)):
        binary = output / variant
        flags = ['-O2'] if variant == 'native' else ['-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
        for command in [['clang++', '-D_GNU_SOURCE', '-std=c++17', *flags, '-Wall', '-Wextra', '-Werror', '-pthread', str(translation), '-o', str(binary)], [str(binary)]]:
            env = os.environ.copy(); env['ASAN_OPTIONS'] = 'detect_leaks=1:alloc_dealloc_mismatch=1'
            result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
            records.append(dict(variant=variant, command=command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
            if result.returncode: break
        if records[-1]['exit_code']: break
    unchanged = all(digest(Path(path)) == expected for path, expected in inputs.items())
    report = dict(passed=unchanged and all(item['exit_code'] == 0 for item in records), input_sha256=inputs,
                  owned_source_sha256=digest(translation), inputs_unchanged=unchanged, commands=records,
                  scope='exact transformed process fanout/exit/transport/teardown methods; native valid seqpacket protocol with adapted identity/setup/monitor ownership; no adversarial RPC, actual dtape, whole runtime or guest claim')
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
