#!/usr/bin/env python3
"""Compile only the original per-thread cwd ABI accessor outside the source build."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve(strict=True)
    output = args.output.resolve()
    source = next(Path(line.partition('=')[2]).resolve() for line in
                  (build/'CMakeCache.txt').read_text().splitlines()
                  if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='))
    if output.is_relative_to(build) or output.is_relative_to(source):
        parser.error('output must be outside reference source and build')
    directory = build/'src/external/xnu/darling/src/libsystem_kernel/emulation'
    flags_file = directory/'CMakeFiles/emulation.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines() if ' = ' in line)
    command = ['/usr/bin/clang', *sum((shlex.split(flags[k]) for k in
               ['C_DEFINES', 'C_INCLUDES', 'C_FLAGS']), [])]
    fixture = Path(__file__).resolve().parent/'fixtures/lstat-bridge.c'
    output.mkdir(parents=True, exist_ok=False)
    target = output/'lstat-bridge.o'
    command += ['-c', str(fixture), '-o', str(target)]
    subprocess.run(command, cwd=directory, check=True)
    record = {'command': command, 'source_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
              'flags_sha256': hashlib.sha256(flags_file.read_bytes()).hexdigest(),
              'object_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    (output/'build.json').write_text(json.dumps(record, indent=2)+'\n')
    print(target)


if __name__ == '__main__':
    main()
