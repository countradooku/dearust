#!/usr/bin/env python3
"""Relink matching existing Darling server objects without modifying their build."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replacements(values):
    result = {}
    for value in values:
        source, sep, target = value.partition('=')
        if not sep or source in result or source.startswith('/') or '..' in Path(source).parts:
            raise ValueError('replacement must be a unique relative source=object')
        result[source] = Path(target).resolve(strict=True)
    return result


def validate_output(build, output):
    source = None
    for line in (build / 'CMakeCache.txt').read_text().splitlines():
        if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='):
            source = Path(line.partition('=')[2]).resolve(strict=True)
            break
    if source is None:
        raise ValueError('CMake cache does not identify the original source tree')
    if output.is_relative_to(build) or output.is_relative_to(source):
        raise ValueError('output must be outside the original source and build trees')


def defined_symbols(path):
    result = subprocess.run(['nm', '-g', '--defined-only', str(path)], check=True,
                            text=True, capture_output=True)
    return result.stdout


def relink(build, output, replace, archives):
    validate_output(build, output)
    server = build / 'src/external/darlingserver'
    duct = server / 'duct-tape'
    archive_command = shlex.split((duct / 'CMakeFiles/darlingserver_duct_tape.dir/link.txt').read_text().splitlines()[0])
    if archive_command[1] != 'qc':
        raise ValueError('unsupported original archive command')
    prefix = 'CMakeFiles/darlingserver_duct_tape.dir/'
    objects, used, provenance = [], set(), []
    for item in archive_command[3:]:
        if not item.startswith(prefix) or not item.endswith('.o'):
            raise ValueError('unexpected archive input: ' + item)
        source = item[len(prefix):-2]
        original = (duct / item).resolve(strict=True)
        selected = replace.get(source, original)
        if source in replace:
            used.add(source)
        objects.append(str(selected))
        provenance.append({'source': source, 'original': str(original),
                           'original_sha256': digest(original), 'selected': str(selected),
                           'selected_sha256': digest(selected), 'replaced': source in replace})
    if used != set(replace):
        raise ValueError('unknown replacements: ' + ', '.join(set(replace) - used))
    output.mkdir(parents=True, exist_ok=False)
    archive = output / 'libdarlingserver_duct_tape.a'
    ar = [archive_command[0], 'qc', str(archive), *objects]
    subprocess.run(ar, check=True)
    subprocess.run([archive_command[0], 's', str(archive)], check=True)
    original_link = shlex.split((server / 'CMakeFiles/darlingserver.dir/link.txt').read_text())
    link = []
    skip = False
    for i, item in enumerate(original_link):
        if skip:
            skip = False
            continue
        if item == '-o':
            link.extend(['-o', str(output / 'darlingserver')])
            skip = True
        elif item == 'duct-tape/libdarlingserver_duct_tape.a':
            link.extend([str(archive), *map(str, archives)])
        elif item.endswith(('.o', '.a')):
            link.append(str((server / item).resolve(strict=True)))
        else:
            link.append(item)
    link.append('-Wl,-Map,' + str(output / 'darlingserver.map'))
    subprocess.run(link, check=True)
    report = {'build': str(build), 'replacements': sorted(used), 'inputs': provenance,
              'extra_archives': [{'path': str(p), 'sha256': digest(p)} for p in archives],
              'link_argv': link, 'archive_argv': ar,
              'server_sha256': digest(output / 'darlingserver'),
              'replacement_defined_symbols': {key: defined_symbols(value)
                                               for key, value in replace.items()},
              'archive_defined_symbols': {str(p): defined_symbols(p) for p in archives},
              'final_defined_symbols': defined_symbols(output / 'darlingserver'),
              'link_map': str(output / 'darlingserver.map')}
    (output / 'relink.json').write_text(json.dumps(report, indent=2) + '\n')
    print(output / 'darlingserver')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replace', action='append', default=[], metavar='SOURCE=OBJECT')
    parser.add_argument('--archive', action='append', type=Path, default=[])
    args = parser.parse_args()
    relink(args.build.resolve(strict=True), args.output.resolve(), replacements(args.replace),
           [p.resolve(strict=True) for p in args.archive])


if __name__ == '__main__':
    main()
