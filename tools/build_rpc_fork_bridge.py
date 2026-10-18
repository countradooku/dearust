#!/usr/bin/env python3
"""Build copied generated RPC wrappers with fork-aware transaction cancellation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
from build_copy_signal_bridge import BASE, replace_once, x86_64_flags


def patch_rpc(text):
    count = 0
    pattern = re.compile(r'(^int dserver_rpc_explicit_[^\n]*\{\n)(.*?)(^};)', re.M | re.S)
    def function(match):
        nonlocal count
        count += 1
        body = match[2]
        atomic = 'dserver_rpc_hooks_atomic_save_t atomic_save;' in body
        cleanup = '\t\tdserver_rpc_hooks_atomic_end(&atomic_save);\n' if atomic else ''
        body = body.replace('char controlbuf[DSERVER_RPC_HOOKS_CMSG_SPACE(sizeof(fds))];',
                            'char controlbuf[DSERVER_RPC_HOOKS_CMSG_SPACE(sizeof(fds))] = {0};')
        for operation, arguments in [('send', 'server_socket, &callmsg'), ('receive', 'server_socket, &replymsg')]:
            old = f'long_status = dserver_rpc_hooks_{operation}_message({arguments});'
            if body.count(old) != 1:
                raise ValueError('unexpected RPC message call in ' + match[1])
            condition = ('(long_status < 0 && dearust_rpc_abandoned(dearust_generation))'
                         if operation == 'receive' else 'dearust_rpc_abandoned(dearust_generation)')
            abandon = ('\n\tif (' + condition + ' || long_status == -4096) {\n'
                       + ('\t\tdearust_rpc_discard_rights(&replymsg, sizeof(controlbuf));\n' if operation == 'receive' else '')
                       + cleanup + '\t\treturn dserver_rpc_hooks_get_interrupt_status();\n\t}')
            body = body.replace(old, f'long_status = dserver_rpc_hooks_{operation}_message({arguments}, dearust_generation);' + abandon)
        body = body.replace('retry_receive:\n',
                            'retry_receive:\n\treplymsg.msg_controllen = sizeof(controlbuf);\n')
        return match[1] + '\tconst unsigned long dearust_generation = dearust_rpc_current_generation();\n' + body + match[3]
    result = pattern.sub(function, text)
    result = result.replace('const dserver_rpc_hooks_msghdr_t* message);',
                            'const dserver_rpc_hooks_msghdr_t* message, unsigned long dearust_generation);')
    result = result.replace('dserver_rpc_hooks_msghdr_t* out_message);',
                            'dserver_rpc_hooks_msghdr_t* out_message, unsigned long dearust_generation);')
    if count != text.count("\nint dserver_rpc_explicit_") or count == 0:
        raise ValueError(f'expected full generated RPC source, found only {count} wrappers')
    return result, count


def patch_definitions(text, helper):
    # The resource header is force-included into this one copied RPC unit.
    anchor = 'static long int dserver_rpc_hooks_send_message('
    index = text.index(anchor)
    text = text[:index] + '#include "' + str(helper) + '"\n' + text[index:]
    text = replace_once(text, 'const dserver_rpc_hooks_msghdr_t* message) {',
                        'const dserver_rpc_hooks_msghdr_t* message, unsigned long dearust_generation) {')
    text = replace_once(text, 'dserver_rpc_hooks_msghdr_t* out_message) {',
                        'dserver_rpc_hooks_msghdr_t* out_message, unsigned long dearust_generation) {\n'
                        '\tconst unsigned long dearust_capacity = out_message->msg_controllen;')
    text = replace_once(text, 'return LINUX_SYSCALL(__NR_sendmsg, socket, message, 0);',
                        'return dearust_rpc_syscall(socket, (long)message, 0, 0, 0, 0, __NR_sendmsg, dearust_generation);')
    text = text.replace('LINUX_SYSCALL(__NR_recvmsg, socket, out_message, 0x40 /* MSG_DONTWAIT */)',
                        'dearust_rpc_syscall(socket, (long)out_message, 0x40, 0, 0, 0, __NR_recvmsg, dearust_generation)')
    text = replace_once(text, 'LINUX_SYSCALL(__NR_recvmsg, socket, out_message, 0)',
                        'dearust_rpc_syscall(socket, (long)out_message, 0, 0, 0, 0, __NR_recvmsg, dearust_generation)')
    # S2C messages can consume ancillary storage before retrying internally.
    # Restore its full initialized capacity for every subsequent receive.
    begin = text.index('static long int dserver_rpc_hooks_receive_message(')
    position = text.index('retry:\n', begin) + len('retry:\n')
    text = text[:position] + ('\t__builtin_memset(out_message->msg_control, 0, dearust_capacity);\n'
                             '\tout_message->msg_controllen = dearust_capacity;\n') + text[position:]
    # An already delivered response is preserved across a fork snapshot.
    marker = '\tif (ret < 0) {\n\t\treturn ret;\n\t}\n\n'
    begin = text.index('static long int dserver_rpc_hooks_receive_message(')
    position = text.index(marker, begin)
    text = text[:position] + '''\tif ((ret < 0 && dearust_rpc_abandoned(dearust_generation)) || ret == -4096) {
        dearust_rpc_discard_rights(out_message, dearust_capacity);
        return -4096;
    }
''' + text[position:]
    text = replace_once(text, 'if (callhdr->call_number == 0x52cca11) {',
                        'if (callhdr->call_number == 0x52cca11) {\n'
                        '\t\t\tif (dearust_rpc_abandoned(dearust_generation)) {\n'
                        '\t\t\t\tdearust_rpc_discard_rights(out_message, dearust_capacity);\n'
                        '\t\t\t\treturn -4096;\n\t\t\t}')
    # Transfer ownership to the S2C parser: it closes this descriptor after mmap.
    anchor = 'dserver_rpc_hooks_memcpy(&mmap_call->fd, DSERVER_RPC_HOOKS_CMSG_DATA(reply_cmsg), sizeof(int));'
    text = replace_once(text, anchor, anchor + '\n\t\t\t\t\t\t*(int *)DSERVER_RPC_HOOKS_CMSG_DATA(reply_cmsg) = -1;')
    old = 'LINUX_SYSCALL(__NR_sendto, socket, &reply, sizeof(reply), 0, dserver_rpc_hooks_get_server_address(), dserver_rpc_hooks_get_server_address_length())'
    if text.count(old) != 4:
        raise ValueError('expected four S2C send sites')
    text = text.replace(old,
                        'dearust_rpc_syscall(socket, (long)&reply, sizeof(reply), 0, (long)dserver_rpc_hooks_get_server_address(), dserver_rpc_hooks_get_server_address_length(), __NR_sendto, dearust_generation)')
    operations = {
        'LINUX_SYSCALL(__NR_mmap, mmap_call->address, mmap_call->length, mmap_call->protection, mmap_call->flags, mmap_call->fd, mmap_call->offset)':
            'dearust_rpc_syscall((long)mmap_call->address, mmap_call->length, mmap_call->protection, mmap_call->flags, mmap_call->fd, mmap_call->offset, __NR_mmap, dearust_generation)',
        'LINUX_SYSCALL2(__NR_munmap, munmap_call->address, munmap_call->length)':
            'dearust_rpc_syscall((long)munmap_call->address, munmap_call->length, 0, 0, 0, 0, __NR_munmap, dearust_generation)',
        'LINUX_SYSCALL3(__NR_mprotect, mprotect_call->address, mprotect_call->length, mprotect_call->protection)':
            'dearust_rpc_syscall((long)mprotect_call->address, mprotect_call->length, mprotect_call->protection, 0, 0, 0, __NR_mprotect, dearust_generation)',
        'LINUX_SYSCALL3(__NR_msync, msync_call->address, msync_call->size, msync_call->sync_flags)':
            'dearust_rpc_syscall((long)msync_call->address, msync_call->size, msync_call->sync_flags, 0, 0, 0, __NR_msync, dearust_generation)',
    }
    for old, new in operations.items():
        text = replace_once(text, old, new)
    text, count = re.subn(r'^#define dserver_rpc_hooks_atomic_save_t [^\n]+',
                          '#define dserver_rpc_hooks_atomic_save_t linux_sigset_t', text, flags=re.M)
    if count != 1:
        raise ValueError('expected one native atomic mask type')
    for name in ('begin', 'end'):
        pattern = re.compile(r'(static void dserver_rpc_hooks_atomic_' + name
                             + r'\(dserver_rpc_hooks_atomic_save_t\* atomic_save\) \{).*?^};', re.M | re.S)
        text, count = pattern.subn(lambda match: match[1] + '\n\tdearust_rpc_mask_' + name + '(atomic_save);\n};', text)
        if count != 1:
            raise ValueError('expected one atomic mask ' + name)
    return text


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve(strict=True)
    source = next(Path(line.partition('=')[2]).resolve() for line in
                  (build/'CMakeCache.txt').read_text().splitlines()
                  if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='))
    output = args.output.resolve()
    if output.is_relative_to(build) or output.is_relative_to(source):
        parser.error('output must be outside reference source and build')
    directory = build/BASE
    flags_file = directory/'CMakeFiles/emulation.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines() if ' = ' in line)
    compiler = ['/usr/bin/clang', *x86_64_flags(sum((shlex.split(flags[key]) for key in ['C_DEFINES','C_INCLUDES','C_FLAGS']), []))]
    output.mkdir(parents=True, exist_ok=False)
    fixture = Path(__file__).resolve().parent/'fixtures'
    rpc = build/'src/external/darlingserver/src/rpc.c'
    definitions = source/BASE/'include/linux_premigration/resources/dserver-rpc-defs.h'
    patched, count = patch_rpc(rpc.read_text())
    if count < 50:
        raise ValueError('expected full generated RPC wrapper source')
    patched = replace_once(patched, '#include "../include/darlingserver/rpc.h"',
                           '#include "' + str(rpc.parent.parent/'include/darlingserver/rpc.h') + '"')
    (output/'rpc.c').write_text(patched)
    (output/'dserver-rpc-defs.h').write_text(patch_definitions(definitions.read_text(),fixture/'rpc-fork-guard.h'))
    units = [('rpc.c', output/'rpc.c',['-include',str(output/'dserver-rpc-defs.h')]),
             ('rpc-fork-state.c',fixture/'rpc-fork-state.c',[]),
             ('rpc-fork-leaf.S',fixture/'rpc-fork-leaf.S',[])]
    commands=[]
    for name,path,extra in units:
        command=compiler+extra+['-c',str(path),'-o',str(output/(name+'.o'))]
        subprocess.run(command,cwd=directory,check=True);commands.append(command)
    files=[rpc,definitions,flags_file,output/'dserver-rpc-defs.h',*[path for _,path,_ in units],fixture/'rpc-fork-guard.h',*output.glob('*.o')]
    report={'wrappers':count,'commands':commands,'sha256':{str(path):digest(path) for path in files}}
    (output/'build.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
