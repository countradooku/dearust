#!/usr/bin/env python3
"""Rebuild every owned server TU with one explicit work-queue configuration.

The lifecycle candidate stays uninstalled. A fresh process temporarily adapts
the existing compiler runner, recording the actual arguments in the ordinary
build manifest as well as a separately hashed configuration report.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--work-queue', required=True, choices=['single', 'multi'])
    args = parser.parse_args()
    candidate, output = args.candidate.resolve(strict=True), args.output.resolve()
    import build_prepared_resource_server as builder
    from build_resource_lifecycle_server import configure
    base = json.loads((candidate/'build.json').read_text())
    source = Path(base['source'])/'src/external/darlingserver'
    header = candidate/'internal-include/darlingserver/config.hpp'
    if not header.is_file(): header = source/'internal-include/darlingserver/config.hpp'
    configure(builder, candidate, output, checked_generation=True, prepared_exec=True)
    builder.EXTRA_TRANSFORM_INPUTS += [Path(__file__).resolve(), header]
    builder.EXTRA_BUILD_RECORDS.append('workqueue-configuration.json')
    macro = int(args.work_queue == 'single')
    selected = '-DDSERVER_SINGLE_THREADED=' + str(macro)
    compiled = []
    original_run = builder.subprocess.run

    def configured_run(command, *positional, **keywords):
        if isinstance(command, list):
            indexes = [index for index, part in enumerate(command)
                       if isinstance(part, str) and part.startswith('-DDSERVER_SINGLE_THREADED=')]
            if indexes:
                if len(indexes) != 1 or Path(command[0]).name not in ('clang', 'clang++') or '-c' not in command:
                    raise ValueError('unexpected work-queue definition outside owned compilation')
                # Mutate the actual command list: the parent builder records
                # these same arguments, rather than its previous configuration.
                command[indexes[0]] = selected
                compiled.append(list(command))
        return original_run(command, *positional, **keywords)

    previous_relink = builder.relink

    def record_configuration(*positional, **keywords):
        previous_relink(*positional, **keywords)
        cpp = [command for command in compiled if Path(command[0]).name == 'clang++']
        if len(cpp) != len(base['server_objects']):
            raise ValueError('not every server translation unit received the requested configuration')
        record = dict(scope=__doc__, work_queue=args.work_queue,
                      DSERVER_SINGLE_THREADED=macro,
                      server_translation_units=len(cpp),
                      selected_config_header=str(header),
                      selected_config_header_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),
                      commands=compiled, resource_ownership_activated=False)
        (output/'workqueue-configuration.json').write_text(json.dumps(record, indent=2)+'\n')

    builder.relink = record_configuration
    builder.subprocess.run = configured_run
    sys.argv = ['build_prepared_resource_server', '--candidate', str(candidate), '--output', str(output)]
    try:
        builder.main()
    finally:
        builder.subprocess.run = original_run


if __name__ == '__main__': main()
