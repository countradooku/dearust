#!/usr/bin/env python3
"""Add guard snapshot hooks to a copied matching fork.c; never edit the source tree."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
from relink_system_kernel import KERNEL, digest


RAW_FORK = '#ifdef SYS_fork\n\tret = LINUX_SYSCALL(__NR_fork);\n#else\n\tret = LINUX_SYSCALL(__NR_clone, LINUX_SIGCHLD, 0);\n#endif'
NATIVE_FORK = '\tret = elfcalls()->fork();'


def patch(text):
    matches = [(anchor, text.count(anchor)) for anchor in (NATIVE_FORK, RAW_FORK)]
    if sum(count for _, count in matches) != 1:
        raise ValueError('requires exactly one supported native or pinned raw fork block')
    anchor = next(anchor for anchor, count in matches if count == 1)
    prepare = '\textern void guard_table_prefork(void);\n\textern void guard_table_postfork_parent(void);\n\tguard_table_prefork();\n'
    complete = '\n\t/* Release on success AND failure; child resets its inherited lock below. */\n\tif (ret != 0) guard_table_postfork_parent();'
    return text.replace(anchor, prepare + anchor + complete)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'build', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    source, build = args.source.resolve(strict=True), args.build.resolve(strict=True)
    output = args.output.resolve()
    for root in (source, build):
        if output.is_relative_to(root) or root.is_relative_to(output):
            parser.error('output must be separate from reference trees')
    original = source/KERNEL/'emulation/src/xnu_syscall/bsd/impl/process/fork.c'
    text = patch(original.read_text())
    directory = build/KERNEL/'emulation'
    flagsfile = directory/'CMakeFiles/emulation.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flagsfile.read_text().splitlines() if ' = ' in line)
    output.mkdir(parents=True, exist_ok=False)
    copied = output/'fork.c'
    copied.write_text(text)
    obj = output/'fork.c.o'
    command = ['/usr/bin/clang', *sum((shlex.split(flags[key]) for key in ('C_DEFINES', 'C_INCLUDES', 'C_FLAGS')), []),
               '-c', str(copied), '-o', str(obj)]
    subprocess.run(command, cwd=directory, check=True)
    record = {'source': str(original), 'source_sha256': digest(original),
              'copied_sha256': digest(copied), 'object': str(obj), 'object_sha256': digest(obj),
              'flags_sha256': digest(flagsfile), 'command': command}
    (output/'build.json').write_text(json.dumps(record, indent=2)+'\n')
    print(obj)


if __name__ == '__main__':
    main()
