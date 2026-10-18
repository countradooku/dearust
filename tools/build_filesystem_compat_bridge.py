#!/usr/bin/env python3
"""Preserve the matching runtime's unported public mount-name helper verbatim.

The four filesystem entries are supplied separately. This object retains only
the local extension preceding sys_statfs, including its original private cache.
It is not used by the migrated entries or the exact pinned C timing reference.
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess

from build_copy_signal_bridge import BASE, x86_64_flags
from relink_system_kernel import digest, symbols


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve(strict=True)
    source = next(Path(line.partition('=')[2]).resolve(strict=True)
                  for line in (build / 'CMakeCache.txt').read_text().splitlines()
                  if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='))
    output = args.output.resolve()
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (source, build)):
        parser.error('output must be separate from original source and build')
    original = source / BASE / 'src/xnu_syscall/bsd/impl/stat/statfs.c'
    text = original.read_text()
    matches = list(re.finditer(r'^long sys_statfs\(', text, re.M))
    if len(matches) != 1:
        raise ValueError('expected one canonical sys_statfs entry boundary')
    prefix = text[:matches[0].start()]
    if len(re.findall(r'^void lolsys_fill_mount_names\(', prefix, re.M)) != 1:
        raise ValueError('matching local public mount helper is required')
    flags_file = build / BASE / 'CMakeFiles/emulation.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines() if ' = ' in line)
    output.mkdir(parents=True, exist_ok=False)
    selected = output / 'filesystem-compat.c'
    selected.write_text(prefix)
    obj = output / 'filesystem-compat.o'
    command = ['/usr/bin/clang', *x86_64_flags(sum((shlex.split(flags[key])
               for key in ('C_DEFINES', 'C_INCLUDES', 'C_FLAGS')), [])),
               '-c', str(selected), '-o', str(obj)]
    subprocess.run(command, cwd=build / BASE, check=True, timeout=120)
    if symbols(obj, '--defined-only') != {'_lolsys_fill_mount_names'}:
        raise ValueError('compatibility object must define only the original public helper')
    visibility = subprocess.check_output(['llvm-nm', '-m', str(obj)], text=True)
    lines = [line for line in visibility.splitlines() if line.endswith(' _lolsys_fill_mount_names')]
    if len(lines) != 1 or 'external' not in lines[0] or 'private external' in lines[0]:
        raise ValueError('matching mount helper must remain public')
    record = {'scope': __doc__, 'original': str(original), 'prefix_bytes': len(prefix.encode()),
              'command': command, 'defined_symbols': ['_lolsys_fill_mount_names'],
              'undefined_symbols': sorted(symbols(obj, '--undefined-only')),
              'sha256': {str(path): digest(path) for path in (original, flags_file, selected, obj)}}
    (output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
