#!/usr/bin/env python3
"""Build copied original signal units with an early guarded-copy fixup hook."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


BASE = Path('src/external/xnu/darling/src/libsystem_kernel/emulation')


def x86_64_flags(flags):
    """Select the supported runtime slice; the relinker keeps original i386.

    Pinned CMake emits both -arch i386 and -arch x86_64, sometimes twice.
    Our new Rust/assembly boundaries support x86_64 only; never compile or
    advertise a new i386 bridge merely because CMake's source object was fat.
    """
    selected = []
    index = 0
    while index < len(flags):
        if flags[index] == '-arch':
            if index + 1 == len(flags):
                raise ValueError('missing architecture argument')
            index += 2
        else:
            selected.append(flags[index])
            index += 1
    return [*selected, '-arch', 'x86_64']


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'expected exactly one original source anchor: {old!r}')
    return text.replace(old, new, 1)


def hook_function(text, name, argument):
    # Match a definition (never a forward declaration), independent of whitespace.
    pattern = re.compile(r'((?:static\s+)?void\s+' + re.escape(name)
                         + r'\s*\([^;{}]*\)\s*\{)')
    if len(pattern.findall(text)) != 1:
        raise ValueError(f'expected exactly one definition of {name}')
    hook = ('\n#if defined(__x86_64__)\n'
            f'\tif (dearust_copy_signal_enter({argument}, info, '
            '(struct linux_ucontext*)ctxt)) return;\n#endif\n'
            '\t/* A forked child checks in as a fresh server thread. It must not\n'
            '\t * pop the parent process signal-interrupt frame on return. */\n'
            '\tlong dearust_signal_process = dearust_signal_process_identity();\n')
    return pattern.sub(lambda match: match.group(1) + hook, text, count=1)


def patch_sigexc(text):
    text = hook_function(text, 'sigexc_handler', 'linux_signum')
    text = hook_function(text, 'sigrt_handler', 'signum')
    exit_call = 'status = dserver_rpc_interrupt_exit();'
    if text.count(exit_call) != 2:
        raise ValueError('expected exactly two original signal interrupt exits')
    text = text.replace(exit_call,
                        'status = (dearust_signal_process_identity() == dearust_signal_process) ? '
                        'dserver_rpc_interrupt_exit() : 0;')
    # Stored application masks/flags remain in the Darwin ABI domain; translate
    # only when invoking Linux, preserving sys_sigaction's Darwin readback.
    text = replace_once(text, 'linux_sigset_t set = sig_masks[linux_signum];',
                        'linux_sigset_t set;\n\t\t\tsigset_bsd_to_linux(&sig_masks[linux_signum], &set);\n\t\t\tset |= ctxt->uc_sigmask;')
    text = replace_once(text, 'sig_flags[linux_signum] & LINUX_SA_NODEFER',
                        'sig_flags[linux_signum] & BSD_SA_NODEFER')
    begin = text.index('void darling_sigexc_self(void)\n{')
    end = text.index('\n}', begin) + 2
    prefix, text, suffix = text[:begin], text[begin:end], text[end:]
    anchor = 'void darling_sigexc_self(void)\n{'
    text = replace_once(text, anchor, anchor + '''
#if defined(__x86_64__)
    /* Reinstalling standard handlers must not discard RT setup already done. */
    __atomic_fetch_and(&dearust_copy_handler_state, ~3u, __ATOMIC_ACQ_REL);
    int dearust_fault_handlers_ok = 1;
#endif
''')
    # Installation failures must leave the conservative kernel-copy path active.
    anchor = '''LINUX_SYSCALL(__NR_rt_sigaction, i,
				&sa, NULL,
				sizeof(sa.sa_mask));'''
    replacement = '''long dearust_install_result = ''' + anchor + '''
#if defined(__x86_64__)
        if (dearust_install_result < 0)
            dearust_fault_handlers_ok = 0;
#else
        (void)dearust_install_result;
#endif'''
    text = replace_once(text, anchor, replacement)
    text = replace_once(text, 'sys_sigaltstack(&newstack, NULL);', '''long dearust_altstack_result = sys_sigaltstack(&newstack, NULL);
#if defined(__x86_64__)
    if (dearust_fault_handlers_ok && dearust_altstack_result == 0)
        __atomic_fetch_or(&dearust_copy_handler_state, 3u, __ATOMIC_RELEASE);
#else
    (void)dearust_altstack_result;
#endif''')
    rt_anchor = '''rv = LINUX_SYSCALL(__NR_rt_sigaction, signum,
			&sa, NULL,
			sizeof(sa.sa_mask));'''
    rt_update = rt_anchor + '''
#if defined(__x86_64__)
    unsigned dearust_rt_bit = signum == SIGNAL_SIGEXC_SUSPEND ? 4u :
        signum == SIGNAL_S2C ? 8u : 0u;
    if (rv >= 0)
        __atomic_fetch_or(&dearust_copy_handler_state, dearust_rt_bit, __ATOMIC_RELEASE);
    else
        __atomic_fetch_and(&dearust_copy_handler_state, ~dearust_rt_bit, __ATOMIC_ACQ_REL);
#endif'''
    # The RT installer is outside darling_sigexc_self's function body.
    prefix = replace_once(prefix, rt_anchor, rt_update)
    readiness = '''
#if defined(__x86_64__)
/* Standard handlers, initial alternate stack, suspend RT, and S2C RT. */
static unsigned dearust_copy_handler_state;
int dearust_copy_signals_ready(void)
{
    return __atomic_load_n(&dearust_copy_handler_state, __ATOMIC_ACQUIRE) == 15u;
}
#endif
'''
    return prefix + text + suffix, readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--rpc-fork-guard', action='store_true')
    args = parser.parse_args()
    build = args.build.resolve(strict=True)
    source = next(Path(line.partition('=')[2]).resolve() for line in
                  (build / 'CMakeCache.txt').read_text().splitlines()
                  if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='))
    output = args.output.resolve()
    if output.is_relative_to(build) or output.is_relative_to(source):
        parser.error('output must be outside reference source and build')
    directory = build / BASE
    flags_file = directory / 'CMakeFiles/emulation.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines()
                 if ' = ' in line)
    compiler = ['/usr/bin/clang', *x86_64_flags(sum((shlex.split(flags[key]) for key in
                ['C_DEFINES', 'C_INCLUDES', 'C_FLAGS']), []))]
    helper = Path(__file__).resolve().parent / 'fixtures/copy-signal-bridge.h'
    inputs = [BASE / 'src/linux_premigration/signal/sigexc.c',
              BASE / 'src/xnu_syscall/bsd/impl/signal/sigaction.c']
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for index, relative in enumerate(inputs):
        original = source / relative
        text = original.read_text()
        if index == 0:
            patched, declarations = patch_sigexc(text)
        else:
            patched = hook_function(text, 'handler_linux_to_bsd_wrapper', 'linux_signum')
            patched = replace_once(patched, 'dserver_rpc_interrupt_exit();',
                                   'if (dearust_signal_process_identity() == dearust_signal_process)\n'
                                   '\t\tdserver_rpc_interrupt_exit();')
            declarations = ''
        if args.rpc_fork_guard:
            declarations += '\nextern void dearust_rpc_fork_child(struct linux_ucontext*);\n'
            if index == 0:
                patched = patched.replace('dserver_rpc_interrupt_exit() : 0;',
                    'dserver_rpc_interrupt_exit() : (dearust_rpc_fork_child((struct linux_ucontext*)ctxt), 0);')
            else:
                patched = replace_once(patched, '\t\tdserver_rpc_interrupt_exit();',
                    '\t\tdserver_rpc_interrupt_exit();\n'
                    '\telse dearust_rpc_fork_child((struct linux_ucontext*)ctxt);')
        # Include after the original header that defines the Linux signal ABI.
        include = '#include <darling/emulation/linux_premigration/signal/sigexc.h>'
        patched = replace_once(patched, include,
                               include + '\n#include "' + str(helper) + '"\n' + declarations)
        target_source = output / original.name
        target_object = output / (original.stem + '.c.o')
        target_source.write_text(patched)
        # Matching builds deliberately pin sigexc's inline stack-changing
        # assembly to -O0. Preserve source-specific options, not only C_FLAGS.
        object_key = str(BASE / 'CMakeFiles/emulation.dir' / relative.relative_to(BASE)) + '.o_OPTIONS'
        custom = []
        for line in flags_file.read_text().splitlines():
            left, separator, right = line.partition(' = ')
            if separator and left == '# Custom options: ' + object_key:
                custom.extend(shlex.split(right))
        command = compiler + custom + ['-c', str(target_source), '-o', str(target_object)]
        subprocess.run(command, cwd=directory, check=True)
        records.append({'source': str(original), 'source_sha256': digest(original),
                        'copied_source': str(target_source),
                        'copied_source_sha256': digest(target_source),
                        'object': str(target_object), 'object_sha256': digest(target_object),
                        'command': command})
    record = {'flags_sha256': digest(flags_file), 'helper_sha256': digest(helper),
              'rpc_fork_guard': args.rpc_fork_guard,
              'objects': records}
    (output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    main()
