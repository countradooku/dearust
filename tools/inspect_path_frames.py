#!/usr/bin/env python3
"""Record generated local stack subtraction and imports for old/new runtime objects.

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
    parser.add_argument('--names', nargs='+', default=['lstat', 'fstatat', 'symlinkat'])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = {}
    for name in args.names:
        if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            parser.error('names must be simple object basenames')
        result[name] = {}
        for variant in ('baseline', 'optimized'):
            obj = getattr(args, variant)/'objects'/(name+'.o')
            text = subprocess.check_output(['llvm-objdump', '--disassemble', '--no-show-raw-insn', str(obj)], text=True)
            (args.output/(name+'-'+variant+'.asm')).write_text(text)
            frames = []
            functions = {}
            boundaries = list(re.finditer(r'^\s*[0-9a-f]+ <([^>]+)>:\s*$', text, flags=re.M))
            for index, boundary in enumerate(boundaries):
                end = boundaries[index+1].start() if index+1 < len(boundaries) else len(text)
                function = text[boundary.end():end]
                # Probe pages and remainder appear before the first foreign call.
                prologue = re.split(r'\bcallq?\b', function, maxsplit=1)[0]
                subtraction = sum(int(value, 0) for value in re.findall(r'subq\s+\$(0x[0-9a-f]+|[0-9]+),\s*%rsp', prologue))
                frames.append(subtraction)
                functions[boundary.group(1)] = {'local_stack_subtraction_bytes': subtraction,
                                                'prologue_push_bytes': 8*len(re.findall(r'\bpushq\b', prologue))}
            if not frames:
                raise ValueError('unrecognized x86_64 prologue in '+str(obj))
            imports = subprocess.check_output(['llvm-nm', '--extern-only', '--undefined-only', '--format=posix', str(obj)], text=True)
            result[name][variant] = {'object_sha256': hashlib.sha256(obj.read_bytes()).hexdigest(),
                                     'object_bytes': obj.stat().st_size,
                                     'max_local_stack_subtraction_bytes': max(frames),
                                     'functions': functions,
                                     'imports': [line.split()[0] for line in imports.splitlines()]}
    report = {'scope': __doc__, 'objects': result}
    (args.output/'frames.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
