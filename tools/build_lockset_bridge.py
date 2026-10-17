#!/usr/bin/env python3
"""Compile isolated XNU ABI/lifecycle bridges, preserving original source/build trees."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one source anchor: {old!r}")
    return text.replace(old, new)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True, help='Darling superproject')
    p.add_argument('--build', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    source, build, out = (v.resolve() for v in (a.source, a.build, a.output))
    if out.is_relative_to(source) or out.is_relative_to(build):
        p.error('output must be outside source and original build')
    out.mkdir(parents=True, exist_ok=False)
    root = source / 'src/external/darlingserver/duct-tape'
    buildroot = build / 'src/external/darlingserver/duct-tape'
    flagsfile = buildroot / 'CMakeFiles/darlingserver_duct_tape.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flagsfile.read_text().splitlines() if ' = ' in line)
    compile_flags = sum((shlex.split(flags[k]) for k in ['C_DEFINES', 'C_INCLUDES', 'C_FLAGS']), [])
    repo = Path(__file__).resolve().parents[1]
    patches = {}
    patches['xnu/osfmk/kern/sync_lock.c'] = (repo / 'crates/darling-lockset/bridge/sync_lock.c').read_text()
    path = 'xnu/osfmk/kern/ipc_sync.c'
    text = (root / path).read_text()
    anchor = 'lock_set_t\nconvert_port_to_lock_set(__unused ipc_port_t port)'
    if text.count(anchor) != 1 or not text.rstrip().endswith('return IP_NULL;\n}'):
        raise ValueError('unexpected ipc_sync converter tail')
    patches[path] = text[:text.index(anchor)]
    path = 'xnu/osfmk/kern/ipc_kobject.c'
    patches[path] = replace_once((root / path).read_text(), '\t\tcase IKOT_SEMAPHORE:\n\t\t\tsemaphore_notify(request_header);', '\t\tcase IKOT_LOCK_SET: {\n\t\t\textern void darling_lockset_notify(mach_msg_header_t *);\n\t\t\tdarling_lockset_notify(request_header);\n\t\t\treturn TRUE;\n\t\t}\n\t\tcase IKOT_SEMAPHORE:\n\t\t\tsemaphore_notify(request_header);')
    for kind in ['task', 'thread']:
        path = f'src/{kind}.c'
        hook = f'\tdtape_psynch_{kind}_destroy({kind});'
        patches[path] = replace_once((root / path).read_text(), hook, hook + f'\n\textern void darling_lockset_{kind}_destroyed(uintptr_t);\n\tdarling_lockset_{kind}_destroyed((uintptr_t)&{kind}->xnu_{kind});')
    dying = 'void dtape_thread_dying(dtape_thread_t* thread) {\n\tthread_lock(&thread->xnu_thread);\n\tthread->xnu_thread.state &= ~(TH_UNINT | TH_WAIT);\n\tthread->xnu_thread.state |= TH_TERMINATE;\n\tthread->xnu_thread.wait_result = THREAD_INTERRUPTED;\n\tclear_wait_internal(&thread->xnu_thread, THREAD_INTERRUPTED);\n\tthread_unlock(&thread->xnu_thread);'
    patches['src/thread.c'] = replace_once(patches['src/thread.c'], dying, dying + '\n\textern void darling_lockset_thread_destroyed(uintptr_t);\n\tdarling_lockset_thread_destroyed((uintptr_t)&thread->xnu_thread);')
    patches['src/task.c'] = replace_once(patches['src/task.c'], 'void dtape_task_dying(dtape_task_t* task) {\n\t// nothing for now', 'void dtape_task_dying(dtape_task_t* task) {\n\ttask_lock(&task->xnu_task);\n\ttask->xnu_task.active = false;\n\ttask_unlock(&task->xnu_task);\n\textern void darling_lockset_task_destroyed(uintptr_t);\n\tdarling_lockset_task_destroyed((uintptr_t)&task->xnu_task);')
    # Mark inactive before registry cleanup, closing create-vs-task-termination races.
    patches['src/task.c'] = patches['src/task.c'].replace('\n\tdarling_lockset_task_destroyed((uintptr_t)&task->xnu_task);', '', 1)
    patches['src/task.c'] = replace_once(patches['src/task.c'], '\tsemaphore_destroy_all(&task->xnu_task);', '\tdarling_lockset_task_destroyed((uintptr_t)&task->xnu_task);\n\tsemaphore_destroy_all(&task->xnu_task);')
    results = []
    for rel, text in patches.items():
        patched = out / rel
        patched.parent.mkdir(parents=True, exist_ok=True)
        patched.write_text(text)
        obj = patched.with_suffix('.c.o')
        command = ['/usr/bin/clang', *compile_flags, '-o', str(obj), '-c', str(patched)]
        subprocess.run(command, cwd=buildroot, check=True)
        results.append({'target': 'duct-tape', 'source': rel, 'object': str(obj), 'command': command})
    # Exec replaces task/thread contexts while retained Mach references can keep
    # the old contexts allocated. Retire ownership at replacement, not refcount zero.
    server_source = root.parent
    server_build = buildroot.parent
    original = (server_source / 'src/process.cpp').read_text()
    patched_text = replace_once(original, '\t\tdtape_thread_release(oldThread);', '\t\tdtape_thread_dying(oldThread);\n\t\tdtape_thread_release(oldThread);')
    patched_text = replace_once(patched_text, '\t\tdtape_task_release(oldTask);', '\t\tdtape_task_dying(oldTask);\n\t\tdtape_task_release(oldTask);')
    patched = out / 'server/src/process.cpp'
    patched.parent.mkdir(parents=True, exist_ok=True)
    patched.write_text(patched_text)
    obj = patched.with_suffix('.cpp.o')
    server_flags = dict(line.split(' = ', 1) for line in (server_build / 'CMakeFiles/darlingserver.dir/flags.make').read_text().splitlines() if ' = ' in line)
    command = ['/usr/bin/clang++', *sum((shlex.split(server_flags[k]) for k in ['CXX_DEFINES', 'CXX_INCLUDES', 'CXX_FLAGS']), []), '-o', str(obj), '-c', str(patched)]
    subprocess.run(command, cwd=server_build, check=True)
    results.append({'target': 'server', 'source': 'src/process.cpp', 'object': str(obj), 'command': command})
    (out / 'bridge-build.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps({'replacements': [v['source']+'='+v['object'] for v in results if v['target'] == 'duct-tape'], 'server_replacements': [v['source']+'='+v['object'] for v in results if v['target'] == 'server']}, indent=2))


if __name__ == '__main__':
    main()
