#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build unchanged Cocotron TextEditor/CGShadingCreate samples and a calculator fixture.

Uses existing Darling source/build headers and installed runtime libraries. Never
runs CMake, modifies inputs, installs applications, or launches their binaries.
--sdk names the Darling runtime root containing System/Library/Frameworks and
usr/lib, not the source SDK header tree. Output must not already exist.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess

SDK_HEADERS = Path('Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/usr/include')



def repair_legacy_keyed_archive(path):
    """Repair XML-style UID dictionaries incorrectly serialized inside a binary nib.

    Only called for the copied CGShadingCreate resource, never reference sources.
    Genuine keyed-archive binary UIDs must use the plist UID primitive.
    """
    before = path.read_bytes()
    archive = plistlib.loads(before)
    if archive.get('$archiver') != 'NSKeyedArchiver' or not isinstance(archive.get('$objects'), list):
        raise ValueError('expected NSKeyedArchiver object archive')
    count = 0

    def convert(value):
        nonlocal count
        if isinstance(value, dict):
            if set(value) == {'CF$UID'}:
                index = value['CF$UID']
                if type(index) is not int or not 0 <= index < len(archive['$objects']):
                    raise ValueError('invalid keyed archive object reference')
                count += 1
                return plistlib.UID(index)
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    converted = convert(archive)
    after = plistlib.dumps(converted, fmt=plistlib.FMT_BINARY) if count else before
    path.write_bytes(after)
    return {'converted_references': count,
            'original_sha256': hashlib.sha256(before).hexdigest(),
            'repaired_sha256': hashlib.sha256(after).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'build', 'sdk', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--clang', default='clang')
    parser.add_argument('--linker', default='ld64.lld')
    parser.add_argument('--repair-shading-nib', action='store_true',
                        help='Repair malformed upstream binary UID references in the copied shading nib')
    args = parser.parse_args()
    try:
        source, build, sdk = (getattr(args, name).resolve(strict=True)
                              for name in ('source', 'build', 'sdk'))
    except OSError as error:
        parser.error(f'missing or inaccessible input: {error}')
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error('--output must be a new, exclusively owned directory')
    resolved_output = output.resolve()
    # Do not permit output inside any supplied input tree, or as its ancestor.
    for path in (source, build, sdk):
        if resolved_output.is_relative_to(path) or path.is_relative_to(resolved_output):
            parser.error('--output must be separate from source, build, and SDK trees')
    sample = source / 'src/external/cocotron/examples/TextEditor'
    gradient = source / 'src/external/cocotron/examples/CGShadingCreate'
    calculator = Path(__file__).resolve().parent / 'fixtures/gui-calculator.m'
    includes = [build / 'src/include', source / 'src/include', source / 'basic-headers',
                source / SDK_HEADERS, build / SDK_HEADERS,
                source / 'framework-include', source / 'framework-private-include']
    required = includes + [sample / name for name in ('main.m', 'MyDocument.m',
                                                      'Info.plist', 'Resources')]
    required += [gradient / name for name in ('main.m', 'GradientView.m', 'GradientView.h', 'Info.plist', 'Resources')]
    required += [calculator, sdk / 'System/Library/Frameworks/AppKit.framework/AppKit',
                 sdk / 'System/Library/Frameworks/Foundation.framework/Foundation',
                 sdk / 'usr/lib/libSystem.dylib', sdk / 'usr/lib/libobjc.dylib']
    for path in required:
        if not path.exists():
            parser.error(f'missing build input: {path}')
    resource = Path(subprocess.check_output([args.clang, '-print-resource-dir'], text=True).strip())
    if not (resource / 'include').is_dir():
        parser.error('Clang builtin header directory is missing')
    compile_args = [args.clang, '-target', 'x86_64-apple-darwin',
                    '-fobjc-runtime=macosx-10.10', '-mmacosx-version-min=11.0',
                    '-nostdinc', '-isystem', str(resource / 'include'), '-DDARLING',
                    '-Wno-nullability-completeness', '-Wno-deprecated-declarations',
                    '-Wno-availability', '-Wno-undef-prefix', '-Wno-expansion-to-defined']
    for path in includes:
        compile_args += ['-I', str(path)]
    for header in ('math.h', 'stdlib.h', 'CoreFoundation/CoreFoundation.h', 'Foundation/Foundation.h'):
        compile_args += ['-include', header]
    link_args = [args.linker, '-arch', 'x86_64', '-platform_version', 'macos', '11.0', '11.0',
                 '-syslibroot', str(sdk), '-L' + str(sdk / 'usr/lib'),
                 '-F' + str(sdk / 'System/Library/Frameworks'), '-framework', 'AppKit',
                 '-framework', 'Foundation', '-framework', 'ApplicationServices', '-lobjc', '-lSystem']
    output.mkdir(parents=True, exist_ok=False)
    commands = []
    resource_repairs = {}

    def run(command):
        commands.append(command)
        with (output / 'build.log').open('a') as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=120)

    for name, sources in [('TextEditor', [sample / 'main.m', sample / 'MyDocument.m']),
                          ('Calculator', [calculator]),
                          ('CGShadingCreate', [gradient / 'main.m', gradient / 'GradientView.m'])]:
        app = output / (name + '.app')
        (app / 'Contents/MacOS').mkdir(parents=True)
        objects = []
        for src in sources:
            obj = output / (name + '-' + src.stem + '.o')
            run(compile_args + ['-c', str(src), '-o', str(obj)])
            objects.append(str(obj))
        run(link_args + objects + ['-o', str(app / 'Contents/MacOS' / name)])
        if name in ('TextEditor', 'CGShadingCreate'):
            upstream = sample if name == 'TextEditor' else gradient
            shutil.copytree(upstream / 'Resources', app / 'Contents/Resources')
            if name == 'CGShadingCreate' and args.repair_shading_nib:
                nib = app / 'Contents/Resources/English.lproj/MainMenu.nib/keyedobjects.nib'
                resource_repairs[str(nib.relative_to(output))] = repair_legacy_keyed_archive(nib)
            info = plistlib.loads((upstream / 'Info.plist').read_bytes())
            info['CFBundleExecutable'] = name
            info['CFBundleName'] = name
        else:
            info = {'CFBundleExecutable': name, 'CFBundleName': name,
                    'CFBundleIdentifier': 'org.darling-rust.gui-calculator',
                    'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1.0',
                    'NSPrincipalClass': 'NSApplication'}
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    # Preserve upstream source license notices next to the artifacts.
    shutil.copy2(sample / 'main.m', output / 'TextEditor-main-source-license.m')
    record = {'scope': 'Build only; GUI launch and rendering require separate execution checks.',
              'resource_repairs': resource_repairs,
              'source': str(source), 'build': str(build), 'sdk': str(sdk), 'commands': commands,
              'source_sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (sample / 'main.m', sample / 'MyDocument.m', calculator, gradient / 'main.m', gradient / 'GradientView.m')},
              'binary_sha256': {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted(output.glob('*.app/Contents/MacOS/*'))}}
    (output / 'build-commands.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'Built TextEditor.app, Calculator.app and CGShadingCreate.app in {output}')


if __name__ == '__main__':
    main()
