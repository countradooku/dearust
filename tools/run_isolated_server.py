#!/usr/bin/env python3
"""Run a command with a replacement Darling server in private user/mount/PID namespaces.

Example: python3 tools/run_isolated_server.py --server /absolute/darlingserver --
python3 tools/check_mac_apps.py ...

The child command must own its prefixes and shut them down before returning.
A private PID namespace additionally ensures residual children die on its exit.
UID/GID 0 inside the namespace map solely to the invoking user's host IDs.
No installation or setuid permissions are modified. No host-root privilege is used.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True, type=Path)
    parser.add_argument('--loader', type=Path,
                        help='optional paired owned mldr, mounted only in the private namespace')
    parser.add_argument('--kernel', type=Path,
                        help='optional owned kernel for the entire private namespace, including bootstrap')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        parser.error('a command is required after --')
    server = args.server.resolve(strict=True)
    if not server.is_file() or not os.access(server, os.X_OK):
        parser.error('server must be an executable regular file')
    loader = args.loader.resolve(strict=True) if args.loader else None
    if loader is not None and (not loader.is_file() or not os.access(loader, os.X_OK)):
        parser.error('loader must be an executable regular file')
    kernel = args.kernel.resolve(strict=True) if args.kernel else None
    if kernel is not None and not kernel.is_file():
        parser.error('kernel must be a regular file')
    # Positional shell arguments preserve paths exactly; no interpolation occurs.
    script = '''set -eu
mount --make-rprivate /
mount --bind "$1" /usr/local/bin/darlingserver
shift
if [ -n "$1" ]; then
    mount --bind "$1" /usr/local/libexec/darling/usr/libexec/darling/mldr
fi
shift
if [ -n "$1" ]; then
    mount --bind "$1" /usr/local/libexec/darling/usr/lib/system/libsystem_kernel.dylib
fi
shift
exec "$@"
'''
    print('Isolated server SHA-256: ' + hashlib.sha256(server.read_bytes()).hexdigest(), flush=True)
    if loader is not None:
        print('Isolated loader SHA-256: ' + hashlib.sha256(loader.read_bytes()).hexdigest(), flush=True)
    if kernel is not None:
        print('Isolated kernel SHA-256: ' + hashlib.sha256(kernel.read_bytes()).hexdigest(), flush=True)
    return subprocess.run(['unshare', '--user', '--map-root-user', '--mount',
                           '--pid', '--fork', '--kill-child', '--mount-proc',
                           'sh', '-c', script, 'isolated-darling-server', str(server),
                           str(loader) if loader is not None else '',
                           str(kernel) if kernel is not None else '',
                           *command]).returncode


if __name__ == '__main__':
    sys.exit(main())
