#!/usr/bin/env python3
"""Record generated local stack subtraction and imports for old/new path objects.

This is assembly evidence, not resident-memory measurement. Register pushes,
callee stack usage and the caller are excluded from the local subtraction count.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'optimized', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = {}
    for name in ('lstat', 'fstatat', 'symlinkat'):
        result[name] = {}
        for variant in ('baseline', 'optimized'):
            obj = getattr(args, variant)/'objects'/(name+'.o')
            text = subprocess.check_output(['llvm-objdump', '--disassemble', '--no-show-raw-insn', str(obj)], text=True)
            (args.output/(name+'-'+variant+'.asm')).write_text(text)
            frames = []
            for function in re.split(r'^\s*[0-9a-f]+ <[^>]+>:\s*$', text, flags=re.M)[1:]:
                # Probe pages and remainder appear before the first foreign call.
                prologue = re.split(r'\bcallq?\b', function, maxsplit=1)[0]
                frames.append(sum(int(value, 0) for value in re.findall(r'subq\s+\$(0x[0-9a-f]+|[0-9]+),\s*%rsp', prologue)))
            if not frames or max(frames) == 0:
                raise ValueError('unrecognized x86_64 prologue in '+str(obj))
            imports = subprocess.check_output(['llvm-nm', '--extern-only', '--undefined-only', '--format=posix', str(obj)], text=True)
            result[name][variant] = {'object_sha256': hashlib.sha256(obj.read_bytes()).hexdigest(),
                                     'object_bytes': obj.stat().st_size,
                                     'max_local_stack_subtraction_bytes': max(frames),
                                     'imports': [line.split()[0] for line in imports.splitlines()]}
    report = {'scope': __doc__, 'objects': result}
    (args.output/'frames.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
