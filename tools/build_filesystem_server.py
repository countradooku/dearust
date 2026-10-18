#!/usr/bin/env python3
"""Build an owned paired server/RPC extension without changing reference trees."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from relink_darlingserver import relink, validate_output


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_generator(text):
    anchor = '\n]\n\nALLOWED_PRIVATE_TYPES = ['
    if text.count(anchor) != 1:
        raise ValueError('expected exactly one RPC call-list terminator')
    addition = '''
    ('filesystem_snapshot', [
        ('descriptor', '@fd'),
        ('directory', '@fd'),
        ('path', 'const unsigned char*', 'uint64_t'),
        ('path_length', 'uint32_t'),
        ('output', 'void*', 'uint64_t'),
        ('mountpoint', 'void*', 'uint64_t'),
    ], [('status', 'int32_t')]),
'''
    return text.replace(anchor, '\n' + addition + anchor)


def link_from_base(build, output, previous, archive, replacement):
    originals = {item['source']: Path(item['selected']).resolve(strict=True)
                 for item in previous['inputs'] if item['replaced']}
    archives = [Path(item['path']).resolve(strict=True) for item in previous['extra_archives']]
    for item in previous['extra_archives']:
        if digest(Path(item['path'])) != item['sha256']:
            raise ValueError('base server archive changed: ' + item['path'])
    relink(build, output / 'linked', originals, archives + [archive], replacement)


def relink_only(a, build, output, base, previous):
    prior = a.relink_only.resolve(strict=True)
    if prior.is_dir():
        prior = prior / 'build.json'
    record = json.loads(prior.read_text())
    if Path(record['build']).resolve() != build or Path(record['base_server_report']).resolve() != base:
        raise ValueError('compiled server must use the same build and base server')
    old_archive = record['host_archive']
    hashes = {name: value for name, value in record['sha256'].items() if name != old_archive}
    for name, value in hashes.items():
        if digest(Path(name)) != value:
            raise ValueError('compiled server input changed: ' + name)
    replacement = {key: Path(value).resolve(strict=True) for key, value in record['server_objects'].items()}
    expected = {item['source'] for item in previous['server_inputs'] if item['source'] is not None}
    if set(replacement) != expected or any(str(path) not in hashes for path in replacement.values()):
        raise ValueError('compiled server requires all original translation units and object hashes')
    archive = a.host_archive.resolve(strict=True)
    hashes[str(archive)] = digest(archive)
    output.mkdir(parents=True, exist_ok=False)
    link_from_base(build, output, previous, archive, replacement)
    record.update(commands=[], sha256=hashes, host_archive=str(archive),
                  reused_compilation=str(prior), server=str(output / 'linked/darlingserver'))
    hashes[str(prior)] = digest(prior)
    (output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', type=Path, required=True)
    p.add_argument('--base-server', type=Path, required=True)
    p.add_argument('--host-archive', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--relink-only', type=Path, help='reuse a verified owned build directory or build.json')
    a = p.parse_args()
    build = a.build.resolve(strict=True)
    output = a.output.resolve()
    validate_output(build, output)
    base = a.base_server.resolve(strict=True)
    if base.is_dir():
        base = base / 'relink.json'
    previous = json.loads(base.read_text())
    if Path(previous['build']).resolve() != build:
        p.error('base server must use this exact matching build')
    for item in previous['inputs'] + previous['server_inputs']:
        if digest(Path(item['selected'])) != item['selected_sha256']:
            raise ValueError('base server input changed: ' + item['selected'])
    if a.relink_only is not None:
        relink_only(a, build, output, base, previous)
        return
    source = next(Path(line.partition('=')[2]).resolve(strict=True)
                  for line in (build / 'CMakeCache.txt').read_text().splitlines()
                  if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='))
    server_source = source / 'src/external/darlingserver'
    server_build = build / 'src/external/darlingserver'
    output.mkdir(parents=True, exist_ok=False)
    generator = server_source / 'scripts/generate-rpc-wrappers.py'
    owned_generator = output / 'generate-rpc-wrappers.py'
    owned_generator.write_text(patch_generator(generator.read_text()))
    header = output / 'include/darlingserver/rpc.h'
    internal = output / 'internal-include/darlingserver/rpc.internal.h'
    rpc = output / 'src/rpc.c'
    commands = []
    command = ['python3', str(owned_generator), str(header), str(internal), str(rpc),
               '../include/darlingserver/rpc.h']
    subprocess.run(command, check=True)
    commands.append(command)
    if 'dserver_callnum_filesystem_snapshot = 83U' not in header.read_text():
        raise ValueError('new call must append83 without renumbering the existing protocol')
    flags_file = server_build / 'CMakeFiles/darlingserver.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines() if ' = ' in line)
    compiler = ['/usr/bin/clang++', '-I' + str(output / 'include'),
                '-I' + str(output / 'internal-include'),
                *sum((shlex.split(flags[k]) for k in ['CXX_DEFINES', 'CXX_INCLUDES', 'CXX_FLAGS']), [])]
    replacement = {}
    inputs = [generator, flags_file, base]
    helper = Path(__file__).resolve().parent / 'filesystem-server-call.inc'
    inputs.append(helper)
    for item in previous['server_inputs']:
        relative = item['source']
        if relative is None:
            continue
        if not relative.endswith('.cpp'):
            raise ValueError('unexpected server translation unit: ' + relative)
        original = server_source / relative
        if relative in previous['server_replacements']:
            # Existing lifecycle bridge records its adjacent copied source.
            original = Path(item['selected']).with_suffix('')
        inputs.append(original)
        text = original.read_text()
        if relative == 'src/call.cpp':
            text += '\n#include "' + str(helper) + '"\n'
        selected = output / relative
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_text(text)
        obj = selected.with_suffix('.cpp.o')
        command = compiler + ['-c', str(selected), '-o', str(obj)]
        subprocess.run(command, cwd=server_build, check=True)
        commands.append(command)
        replacement[relative] = obj
        inputs += [selected, obj]
    archive = a.host_archive.resolve(strict=True)
    link_from_base(build, output, previous, archive, replacement)
    inputs += [header, internal, rpc, archive]
    report = {'build': str(build), 'base_server_report': str(base), 'commands': commands,
              'protocol': {'call': 83, 'name': 'filesystem_snapshot', 'paired_server_required': True},
              'host_archive': str(archive),
              'server_objects': {key: str(value) for key, value in replacement.items()},
              'generated_files': [str(header), str(internal), str(rpc)],
              'sha256': {str(path): digest(path) for path in inputs},
              'server': str(output / 'linked/darlingserver')}
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
