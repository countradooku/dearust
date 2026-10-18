#!/usr/bin/env python3
"""Build the appended filesystem RPC adapter with existing fork/cancellation guards.

Only owned copies are generated. Matching RPC entries1..82 are checked unchanged;
pinned's older80-call header is explicitly rebased to that server protocol.
filesystem_snapshot is appended as managed call83. Both original runtime builds
retain the same three transaction-guard objects and original compiler flags for
its supported x86_64 slice.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

from build_copy_signal_bridge import BASE, x86_64_flags
from build_rpc_fork_bridge import patch_definitions, patch_rpc


ADAPTER = '''
/* Dearust filesystem metadata pressure path. Local storage only, no guest refs. */
#include <darling/emulation/conversion/errno.h>
__attribute__((visibility("hidden")))
long dearust_filesystem_server_snapshot(int fd, int directory,
    const unsigned char* path, uint32_t path_length,
    unsigned char* output, unsigned char* mountpoint) {
    int32_t status = 0;
    int transport = dserver_rpc_filesystem_snapshot(fd, directory, path,
        path_length, output, mountpoint, &status);
    if (transport < 0) return errno_linux_to_bsd(transport);
    if (transport != 0 || status > 0) return -5;
    return status;
}
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calls(header):
    pairs = re.findall(r'^\s*dserver_callnum_(\w+)\s*=\s*([^,\n]+),', header, re.M)
    return {name: re.sub(r'\s+', '', value) for name, value in pairs
            if name not in ('s2c', 'push_reply', 'invalid')}


def wrappers(text):
    pattern = re.compile(r'^int (dserver_rpc_(?:explicit_)?[^\n(]+)\([^\n]*\) \{\n.*?^};', re.M | re.S)
    return {match[1]: match[0] for match in pattern.finditer(text)}


def check_protocol(original_header, original_rpc, protocol_header, protocol_rpc):
    before, after = calls(original_header), calls(protocol_header)
    if len(before) not in (80, 82) or len(after) != 83:
        raise ValueError('expected original80/82 calls and extended83 calls')
    if after.get('filesystem_snapshot') != '83U':
        raise ValueError('filesystem_snapshot must be appended as managed call83')
    rebased = len(before) == 80
    if not rebased:
        if {name: after.get(name) for name in before} != before:
            raise ValueError('existing matching RPC call numbers or managed flags changed')
    else:
        if set(after)-set(before) != {'fileport_makeport', 'fileport_makefd', 'filesystem_snapshot'}:
            raise ValueError('unexpected legacy80 protocol difference')
        for name, value in before.items():
            match = re.fullmatch(r'(DSERVER_CALL_UNMANAGED_FLAG\|)?(\d+)U', value)
            if match is None:
                raise ValueError('unexpected legacy call-number expression')
            number = int(match[2])
            expected = (match[1] or '')+str(number+(2 if number >= 30 else 0))+'U'
            if after.get(name) != expected:
                raise ValueError('legacy protocol differs beyond two inserted fileport calls')
    old_functions, new_functions = wrappers(original_rpc), wrappers(protocol_rpc)
    if len(old_functions) != 2*len(before) or len(new_functions) != 166:
        raise ValueError('expected two wrappers for every original/extended call')
    additions = []
    for name, old in old_functions.items():
        new = new_functions.get(name)
        if old == new:
            continue
        if name != 'dserver_rpc_explicit_interrupt_enter' or new is None:
            raise ValueError('existing generated RPC wrapper changed: '+name)
        # The generated interruption reply union includes every protocol reply.
        # Pinned's old generator additionally omitted the two fileport replies;
        # restoring union members changes no field layout on the wire.
        removable = ('filesystem_snapshot', 'fileport_makeport', 'fileport_makefd')
        normalized = new
        removed = []
        for reply in removable:
            line = '\t\tdserver_rpc_reply_'+reply+'_t potential_reply_'+reply+';\n'
            if line in normalized and line not in old:
                normalized = normalized.replace(line, '', 1)
                removed.append(reply)
        if normalized != old:
            raise ValueError('interrupt_enter changed beyond generated reply union growth')
        additions.extend(removed)
    return dict(original_calls=len(before), extended_calls=83, original_wrapper_bodies_preserved=len(old_functions),
                original_header_call_numbers_preserved=not rebased,
                legacy80_header_rebased_for_matching_server=rebased,
                permitted_interrupt_reply_union_additions=additions,
                filesystem_call_number=83, existing_call_values=before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'protocol', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    build, protocol = args.build.resolve(strict=True), args.protocol.resolve(strict=True)
    source = next(Path(line.partition('=')[2]).resolve() for line in
        (build/'CMakeCache.txt').read_text().splitlines()
        if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='))
    output = args.output.resolve()
    if any(output.is_relative_to(path) or path.is_relative_to(output) for path in (build, source, protocol)):
        parser.error('output must be separate from reference source, build and protocol trees')
    directory = build/BASE
    flags_file = directory/'CMakeFiles/emulation.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines() if ' = ' in line)
    compiler = ['/usr/bin/clang', '-I'+str(protocol/'include'), *x86_64_flags(sum(
        (shlex.split(flags[key]) for key in ('C_DEFINES', 'C_INCLUDES', 'C_FLAGS')), []))]
    original_rpc = build/'src/external/darlingserver/src/rpc.c'
    original_header = build/'src/external/darlingserver/include/darlingserver/rpc.h'
    rpc, rpc_header = protocol/'src/rpc.c', protocol/'include/darlingserver/rpc.h'
    definitions = source/BASE/'include/linux_premigration/resources/dserver-rpc-defs.h'
    checks = check_protocol(original_header.read_text(), original_rpc.read_text(),
                            rpc_header.read_text(), rpc.read_text())
    patched, count = patch_rpc(rpc.read_text())
    if count != 83:
        raise ValueError('all83 explicit RPC wrappers must retain transaction patches')
    patched, include_count = re.subn(r'^#include\s+(?:"\.\./include/darlingserver/rpc\.h"|\.\./include/darlingserver/rpc\.h)\s*$',
        lambda match: '#include "'+str(rpc_header)+'"', patched, flags=re.M)
    if include_count != 1:
        raise ValueError('expected one generated RPC header include')
    output.mkdir(parents=True, exist_ok=False)
    fixture = Path(__file__).resolve().parent/'fixtures'
    (output/'rpc.c').write_text(patched+ADAPTER)
    (output/'dserver-rpc-defs.h').write_text(patch_definitions(definitions.read_text(), fixture/'rpc-fork-guard.h'))
    units = [('rpc.c', output/'rpc.c', ['-include', str(output/'dserver-rpc-defs.h')]),
             ('rpc-fork-state.c', fixture/'rpc-fork-state.c', []),
             ('rpc-fork-leaf.S', fixture/'rpc-fork-leaf.S', [])]
    commands = []
    for name, path, extra in units:
        command = compiler+extra+['-c', str(path), '-o', str(output/(name+'.o'))]
        subprocess.run(command, cwd=directory, check=True, timeout=120)
        commands.append(command)
    symbol_command = ['llvm-nm', '-m', str(output/'rpc.c.o')]
    symbols = subprocess.check_output(symbol_command, text=True)
    adapter_symbols = [line for line in symbols.splitlines() if '_dearust_filesystem_server_snapshot' in line]
    if len(adapter_symbols) != 1 or 'private external' not in adapter_symbols[0]:
        raise ValueError('filesystem adapter must be one hidden external Mach-O definition')
    files = [original_rpc, original_header, rpc, rpc_header, definitions, flags_file,
             output/'dserver-rpc-defs.h', fixture/'rpc-fork-guard.h',
             *[path for _, path, _ in units], *sorted(output.glob('*.o'))]
    report = dict(build=str(build), protocol=str(protocol), wrappers=count, protocol_checks=checks,
        commands=commands, symbol_command=symbol_command, adapter_symbols=adapter_symbols,
        sha256={str(path):digest(path) for path in files})
    (output/'symbols.txt').write_text(symbols)
    (output/'build.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
