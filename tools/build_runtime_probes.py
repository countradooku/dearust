#!/usr/bin/env python3
"""Compile C runtime regression probes against matching Darling headers/libraries.

Produces tests/<SDK-version>/<name>.bin for check_mac_apps.py. Expected output
files must be provided separately; this builder never derives goldens from a run.
"""
import argparse
import json
from pathlib import Path
import subprocess
from build_gui_apps import SDK_HEADERS
from relink_system_kernel import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'build', 'sdk', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--input', required=True, action='append', type=Path)
    parser.add_argument('--sdk-version', default='10.8')
    args = parser.parse_args()
    source, build, sdk = [getattr(args, name).resolve(strict=True) for name in ('source', 'build', 'sdk')]
    output = args.output.resolve()
    for root in (source, build, sdk):
        if output.is_relative_to(root) or root.is_relative_to(output):
            parser.error('output must be separate from reference trees')
    if Path(args.sdk_version).name != args.sdk_version:
        parser.error('SDK version must be a directory basename')
    inputs = [path.resolve(strict=True) for path in args.input]
    if len({path.name for path in inputs}) != len(inputs):
        parser.error('probe filenames must be unique')
    output.mkdir(parents=True, exist_ok=False)
    (output/args.sdk_version).mkdir()
    resource = subprocess.check_output(['clang', '-print-resource-dir'], text=True).strip()
    compile_args = ['clang', '-target', 'x86_64-apple-darwin', '-mmacosx-version-min=11.0',
                    '-nostdinc', '-isystem', resource+'/include', '-DDARLING', '-DPLATFORM_MacOSX',
                    '-Wno-nullability-completeness', '-Wno-undef-prefix']
    for path in (build/'src/include', source/'src/include', source/'basic-headers',
                 source/SDK_HEADERS, build/SDK_HEADERS):
        compile_args.extend(['-I', str(path)])
    commands = []
    for path in inputs:
        obj = output/(path.name+'.o')
        target = output/args.sdk_version/(path.name+'.bin')
        commands.extend([compile_args+['-c', str(path), '-o', str(obj)],
                         ['ld64.lld', '-arch', 'x86_64', '-platform_version', 'macos', '11.0', '11.0',
                          '-syslibroot', str(sdk), '-L'+str(sdk/'usr/lib'), '-lSystem',
                          str(obj), '-o', str(target)]])
        for command in commands[-2:]:
            subprocess.run(command, check=True, timeout=120)
    report = {'commands': commands, 'sources': {str(p): digest(p) for p in inputs},
              'binaries': {str(p.relative_to(output)): digest(p) for p in sorted((output/args.sdk_version).iterdir())}}
    (output/'build.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
