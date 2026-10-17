#!/usr/bin/env python3
"""Exercise installed Mac utilities with data roundtrips in owned prefixes.

Run through run_isolated_server.py when testing a replacement server. This uses
check_mac_apps' isolation, exact output/status checks and kernel mapping evidence.
Each case creates and removes its own guest temporary directory.
"""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from check_mac_apps import digest, variant

# Expected output is specified independently of execution. set -e propagates every
# failed utility/comparison; no pipeline is used to hide a failing exit status.
CASES = {
    'plistbuddy': (['usr/libexec/PlistBuddy'], '''/usr/libexec/PlistBuddy -c 'Add :name string Dearust' data.plist >/dev/null
/usr/libexec/PlistBuddy -c 'Add :count integer 42' data.plist
/usr/libexec/PlistBuddy -c 'Set :name migrated' data.plist
[ "$(/usr/libexec/PlistBuddy -c 'Print :name' data.plist)" = migrated ]
[ "$(/usr/libexec/PlistBuddy -c 'Print :count' data.plist)" = 42 ]
/usr/libexec/PlistBuddy -c 'Delete :name' data.plist
'''),
    'bsdtar': (['usr/bin/bsdtar'], '''mkdir source restored
printf 'Dearust archive\\n' > source/data
ln -s data source/link
/usr/bin/bsdtar -cf data.tar source
/usr/bin/bsdtar -xf data.tar -C restored
cmp source/data restored/source/data
[ "$(readlink restored/source/link)" = data ]
'''),
    'ditto': (['usr/bin/ditto'], '''mkdir source
printf 'Dearust copy\\n' > source/data
ln -s data source/link
/usr/bin/ditto -rsrc source restored
cmp source/data restored/data
[ "$(readlink restored/link)" = data ]
'''),
    'gzip': (['usr/bin/gzip'], '''printf 'Dearust compression\\nDearust compression\\n' > input
/usr/bin/gzip -c input > compressed.gz
/usr/bin/gzip -t compressed.gz
/usr/bin/gzip -dc compressed.gz > restored
cmp input restored
'''),
    'bzip2': (['bin/bzip2'], '''printf 'Dearust compression\\nDearust compression\\n' > input
/bin/bzip2 -c input > compressed.bz2
/bin/bzip2 -t compressed.bz2
/bin/bzip2 -dc compressed.bz2 > restored
cmp input restored
'''),
    'xz': (['usr/bin/xz'], '''printf 'Dearust compression\\nDearust compression\\n' > input
/usr/bin/xz -c input > compressed.xz
/usr/bin/xz -t compressed.xz
/usr/bin/xz -dc compressed.xz > restored
cmp input restored
'''),
    'zip': (['usr/bin/zip', 'usr/bin/unzip'], '''mkdir source restored
printf 'Dearust zip\\n' > source/data
/usr/bin/zip -q -r data.zip source
/usr/bin/unzip -tq data.zip >/dev/null
/usr/bin/unzip -q data.zip -d restored
cmp source/data restored/source/data
'''),
    'openssl': (['usr/bin/openssl'], '''printf abc > input
/usr/bin/openssl dgst -sha256 input > digest
/bin/grep -q ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad digest
/usr/bin/openssl base64 -in input -out encoded
/usr/bin/openssl base64 -d -in encoded -out restored
cmp input restored
'''),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('original-kernel', 'rust-kernel', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--runtime', type=Path, default=Path('/usr/local/libexec/darling'))
    parser.add_argument('--launcher', default='/usr/local/bin/darling')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--cases', nargs='+', choices=list(CASES), default=list(CASES))
    args = parser.parse_args()
    args.output = args.output.absolute()
    args.output.mkdir(parents=True, exist_ok=False)
    tests = args.output / 'fixtures'
    (tests / 'scripts').mkdir(parents=True)
    binaries = {}
    for name in args.cases:
        paths, commands = CASES[name]
        for relative in paths:
            path = args.runtime / relative
            if not path.is_file():
                parser.error('missing installed utility: ' + str(path))
            binaries[relative] = {'sha256': digest(path),
                                  'kind': 'shell wrapper' if path.read_bytes().startswith(b'#!') else 'binary'}
        script = tests / 'scripts' / (name + '.bin')
        script.write_text("#!/bin/bash\nset -eu\nwork=$(mktemp -d /tmp/dearust-utility.XXXXXX)\ntrap 'rm -rf \"$work\"' EXIT\ncd \"$work\"\n" + commands + "printf '" + name + " PASS\\n'\n")
        script.chmod(0o755)
        (tests / (name + '.stdout')).write_text(name + ' PASS\n')
    config = SimpleNamespace(**vars(args), tests=tests, sdk='scripts')
    records = [variant(config, name, kernel, args.output / name) for name, kernel in
               [('original', args.original_kernel), ('rust', args.rust_kernel)]]
    passed = all(record.get('kernel_mappings') and len(record['results']) == len(args.cases)
                 and all(result['strict_pass'] for result in record['results']) for record in records)
    report = {'scope': 'Installed Mac utilities (ditto is a shell wrapper); synthetic offline data roundtrips only.',
              'utilities_sha256': binaries, 'variants': records, 'passed': passed}
    (args.output / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
