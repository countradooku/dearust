#!/usr/bin/env python3
"""Run installed Darling app comparisons in newly owned, isolated prefixes.

Never overwrites the installation or ~/.darling. Kernel paths must match the
installed server protocol. Strict output and explicitly normalized diagnostics
are reported separately. Prefixes are retained for inspection after shutdown.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

CASES = ('objc_helloworld.m', 'pthread_threads.c', 'tls1.c', 'fork_wait.c',
         'fcntl.c', 'mach_semaphore.c', 'mach_lockset_basic.c',
         'mach_lockset_handoff.c', 'cfbridge.m', 'cfurl_fm_basic.c', 'libproc.c')
NSLOG = re.compile(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ cfbridge\.m\.bin\[\d+:[0-9a-f]+\] Test\n', re.M)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reap_launcher(process, grace=5):
    """Give namespace shutdown time to finish; report denied signals/unreaped PIDs."""
    record = {'pid': process.pid}
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            record['signal_race'] = 'process group exited before signal'
        except PermissionError as error:
            # The Darling setuid launcher can retain real uid 0. Never fall back
            # to name-based killing or another user's/default prefix.
            record['signal_error'] = str(error)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            record['error'] = 'launcher did not exit after bounded cleanup'
    record.update(reaped=process.returncode is not None, exit_code=process.returncode)
    return record


def run_process(argv, env, timeout, on_timeout=None):
    """Kill and reap the launcher process group on timeout; never kill by name."""
    start = time.monotonic()
    # Daemons started by the launcher may inherit its descriptors after setsid.
    # Temporary files avoid communicate() hanging on those unrelated open pipes.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, env=env, stdout=out, stderr=err,
                                   start_new_session=True)
        timed_out = False
        cleanup = None
        timeout_shutdown = None
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if on_timeout is not None:
                try:
                    timeout_shutdown = on_timeout()
                except (OSError, subprocess.SubprocessError) as error:
                    timeout_shutdown = {'error': str(error)}
            cleanup = reap_launcher(process, grace=0 if on_timeout is None else 5)
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(), err.read()
    return {'exit_code': process.returncode, 'timed_out': timed_out,
            'stdout': stdout.decode(errors='replace'),
            'stderr': stderr.decode(errors='replace'),
            'seconds': round(time.monotonic() - start, 3),
            'cleanup': cleanup, 'timeout_shutdown': timeout_shutdown}


def classify(launcher, stdout, status, expected, name):
    # A completed child cannot rescue a launcher timeout/transport failure.
    strict = (not launcher['timed_out'] and launcher['exit_code'] == 0
              and status == 0 and stdout == expected)
    normalized = NSLOG.sub('', stdout) if name == 'cfbridge.m' else stdout
    return {'strict_pass': strict,
            'diagnostic_normalization': 'cfbridge timestamped NSLog Test line' if name == 'cfbridge.m' else None,
            'normalized_pass': (not launcher['timed_out'] and launcher['exit_code'] == 0
                                and status == 0 and normalized == expected)}


def shell_capture(argv, output, name):
    stdout = output / (name + '.stdout')
    stderr = output / (name + '.stderr')
    status = output / (name + '.exit')
    for path in (stdout, stderr, status):
        path.unlink(missing_ok=True)
    mac = lambda path: shlex.quote('/Volumes/SystemRoot' + str(path))
    command = (shlex.join(argv) + ' > ' + mac(stdout) + ' 2> ' + mac(stderr)
               + '; result=$?; printf \'%s\\n\' "$result" > ' + mac(status)
               + '; exit "$result"')
    return command, stdout, stderr, status


def mapped_kernel(staged):
    """Record actual mappings of this uniquely owned copy, not install paths."""
    mappings = []
    inode = staged.stat().st_ino
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            lines = (process / 'maps').read_text().splitlines()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        matching = []
        for line in lines:
            fields = line.split(maxsplit=5)
            if len(fields) == 6 and fields[4] == str(inode) and fields[5] == str(staged):
                matching.append(line)
        if matching:
            mappings.append({'pid': int(process.name), 'mappings': matching})
    return mappings


def variant(args, name, kernel, output):
    output.mkdir()
    # Leave prefix absent for launcher's own account/database initialization.
    reservation = Path(tempfile.mkdtemp(prefix='dr-harness-', dir='/tmp'))
    prefix = reservation / 'p'
    env = {key: value for key, value in os.environ.items()
           if key in ('PATH', 'HOME', 'USER', 'LOGNAME', 'DISPLAY', 'XAUTHORITY')}
    env.update(DPREFIX=str(prefix), LC_ALL='C')
    record = {'variant': name, 'prefix': str(prefix), 'kernel_sha256': digest(kernel),
              'scope': 'Installed locally modified Darling; kernel slices only', 'results': []}
    try:
        record['setup'] = run_process([args.launcher, '--version'], env, args.timeout)
        if record['setup']['exit_code'] != 0 or record['setup']['timed_out']:
            raise RuntimeError('prefix setup failed')
        temporary = prefix / 'private/tmp'
        temporary.mkdir(parents=True, exist_ok=True)
        temporary.chmod(0o1777)
        (prefix / 'home').mkdir()
        staged = prefix / 'usr/lib/system/libsystem_kernel.dylib'
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kernel, staged)
        staged.chmod(0o755)
        record['staged_kernel_sha256'] = digest(staged)
        for case in args.cases:
            executable = args.tests / args.sdk / (case + '.bin')
            expected = (args.tests / (case + '.stdout')).read_text()
            command, stdout, stderr, status = shell_capture(
                ['/Volumes/SystemRoot' + str(executable)], output, case)
            launcher = run_process([args.launcher, 'shell', '/bin/bash', '-c', command], env, args.timeout,
                                   on_timeout=lambda: run_process([args.launcher, 'shutdown'], env, 5))
            try:
                child_status = int(status.read_text().strip())
            except (FileNotFoundError, ValueError):
                child_status = None
            text = stdout.read_text(errors='replace') if stdout.exists() else ''
            result = {'name': case, 'executable_sha256': digest(executable),
                      'expected_stdout_sha256': digest(args.tests / (case + '.stdout')),
                      'launcher': launcher, 'child_exit_code': child_status,
                      'stdout': text, 'stderr': stderr.read_text(errors='replace') if stderr.exists() else '',
                      'expected_stdout': expected,
                      **classify(launcher, text, child_status, expected, case)}
            record['results'].append(result)
            print(name, case, 'PASS' if result['strict_pass'] else 'FAIL', child_status, flush=True)
            (output / 'results.json').write_text(json.dumps(record, indent=2) + '\n')
            if 'kernel_mappings' not in record:
                record['kernel_mappings'] = mapped_kernel(staged)
            if launcher['timed_out']:
                # Daemon-owned children can outlive a launcher; do not start more.
                break
    finally:
        if prefix.exists():
            record['shutdown'] = run_process([args.launcher, 'shutdown'], env, args.timeout)
        (output / 'results.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher', default='/usr/local/bin/darling')
    parser.add_argument('--original-kernel', type=Path, required=True)
    parser.add_argument('--rust-kernel', type=Path, required=True)
    parser.add_argument('--tests', type=Path, required=True, help='tests/src/bin/x86-64 directory')
    parser.add_argument('--sdk', default='10.8')
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    args = parser.parse_args()
    args.tests = args.tests.resolve(strict=True)
    for kernel in (args.original_kernel, args.rust_kernel):
        if not kernel.is_file():
            parser.error('kernel is not a regular file: ' + str(kernel))
    args.output = args.output.absolute()
    args.output.mkdir(parents=True, exist_ok=False)
    records = [variant(args, name, kernel, args.output / name)
               for name, kernel in [('original', args.original_kernel), ('rust', args.rust_kernel)]]
    paired = []
    for original, rust in zip(records[0]['results'], records[1]['results']):
        paired.append({'name': original['name'],
                       'same_child_exit': original['child_exit_code'] == rust['child_exit_code'],
                       'same_stdout': original['stdout'] == rust['stdout'],
                       'same_normalized_stdout': (NSLOG.sub('', original['stdout']) == NSLOG.sub('', rust['stdout'])
                                                  if original['name'] == 'cfbridge.m' else original['stdout'] == rust['stdout']),
                       'same_stderr': original['stderr'] == rust['stderr'],
                       'same_strict_pass': original['strict_pass'] == rust['strict_pass'],
                       'same_normalized_pass': original['normalized_pass'] == rust['normalized_pass']})
    (args.output / 'comparison.json').write_text(json.dumps({'variants': records, 'comparison': paired}, indent=2) + '\n')
    return 0 if all(v.get('kernel_mappings') and len(v['results']) == len(args.cases)
                    and all(r['strict_pass'] for r in v['results']) for v in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
