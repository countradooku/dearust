#!/usr/bin/env python3
"""Run a command with a replacement Darling server in private user/mount namespaces.

Example: python3 tools/run_isolated_server.py --server /absolute/darlingserver --
python3 tools/check_mac_apps.py ...

The child command must own its prefixes and shut them down before returning.
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
    # Positional shell arguments preserve paths exactly; no interpolation occurs.
    script = '''set -eu
mount --make-rprivate /
mount --bind "$1" /usr/local/bin/darlingserver
shift
exec "$@"
'''
    print('Isolated server SHA-256: ' + hashlib.sha256(server.read_bytes()).hexdigest(), flush=True)
    return subprocess.run(['unshare', '--user', '--map-root-user', '--mount',
                           'sh', '-c', script, 'isolated-darling-server', str(server),
                           *command]).returncode


if __name__ == '__main__':
    sys.exit(main())
