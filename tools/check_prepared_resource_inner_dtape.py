#!/usr/bin/env python3
"""Build real inner dtape malloc-failure tests using exact selected C bridges.

No RPC/messages. Only six proved-safe pre-IPC/returned-error allocation boundaries
are denied; the audit explicitly retains panic-prone later IPC/importance/psynch
paths as unfinished. The ordinary actual lifecycle validation also runs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('inner native test anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def instrument_c(text, kind):
    declarations = '\nextern void dearust_inner_enter(unsigned);\nextern void dearust_inner_leave(unsigned);\n'
    if kind == 1:
        signature = 'dtape_task_t* dtape_task_create('
        call = 'dearust_inner_task_body(parent_task, nsid, context, architecture)'
        wrapper = 'dtape_task_t* dtape_task_create(dtape_task_t* parent_task, uint32_t nsid, void* context, dserver_rpc_architecture_t architecture)'
        renamed = 'static dtape_task_t* dearust_inner_task_body('
    elif kind == 2:
        signature = 'dtape_thread_t* dtape_thread_create('
        call = 'dearust_inner_thread_body(task, nsid, context)'
        wrapper = 'dtape_thread_t* dtape_thread_create(dtape_task_t* task, uint64_t nsid, void* context)'
        renamed = 'static dtape_thread_t* dearust_inner_thread_body('
    else:
        signature = 'dtape_semaphore_t* dtape_semaphore_create('
        call = 'dearust_inner_semaphore_body(owning_task, initial_value)'
        wrapper = 'dtape_semaphore_t* dtape_semaphore_create(dtape_task_t* owning_task, int initial_value)'
        renamed = 'static dtape_semaphore_t* dearust_inner_semaphore_body('
    text = once(text, signature, declarations + renamed)
    result_type = wrapper.split('*', 1)[0] + '*'
    text += f'\n{wrapper} {{\n    dearust_inner_enter({kind});\n    {result_type} result = {call};\n    dearust_inner_leave({kind});\n    return result;\n}}\n'
    if kind == 1:
        text += '''unsigned long long dearust_inner_task_state(void* raw, unsigned field) {
    task_t task = &((dtape_task_t*)raw)->xnu_task;
    switch (field) {
        case 0: return task->thread_count;
        case 1: return task->active_thread_count;
        case 2: return task->semaphores_owned;
        case 3: return os_ref_get_count(&task->ref_count);
        default: __builtin_trap();
    }
}
'''
    return text


def instrument_server(files, native_main):
    from patch_prepared_resource_actual_cleanup import patch_actual_cleanup_validation
    files = patch_actual_cleanup_validation(files, native_main)
    files['server.hpp'] = once(files['server.hpp'], '        void _runActualPreparedCleanupValidation();',
        '        void _runActualPreparedCleanupValidation();\n        void _runInnerDtapeAllocationValidation();')
    files['server.cpp'] = once(files['server.cpp'], '\t_runActualPreparedCleanupValidation();',
                              '\t_runInnerDtapeAllocationValidation();')
    files['server.cpp'] = 'bool dearust_inner_allocation_denied() noexcept;\n' + files['server.cpp']
    files['server.cpp'] = once(files['server.cpp'],
        'bool dearust_actual_cleanup_allocation_denied() noexcept {',
        'bool dearust_actual_cleanup_allocation_denied() noexcept {\n'
        '    if (dearust_inner_allocation_denied()) return true;')
    files['server.cpp'] += '\n' + Path(__file__).with_name('prepared-resource-inner-dtape-validation.inc').read_text()
    main = files['darlingserver.cpp']
    main = ('void dearust_inner_malloc_acquired(void*) noexcept;\n'
            'void dearust_inner_allocation_freed(void*) noexcept;\n' + main)
    main = once(main, '    return __libc_malloc(size);',
                '    void* result = __libc_malloc(size);\n'
                '    dearust_inner_malloc_acquired(result);\n    return result;')
    main += '''\nextern "C" void __libc_free(void*) noexcept;
extern "C" void free(void* pointer) noexcept {
    dearust_inner_allocation_freed(pointer);
    __libc_free(pointer);
}
'''
    files['darlingserver.cpp'] = main
    return files


def internal_build(candidate, output, diagnose_prior=False):
    import build_prepared_resource_server as builder
    from patch_prepared_resource_inner_dtape import patch_inner_dtape
    production = builder.patch_registration
    native_main = (candidate / 'src/darlingserver.cpp').read_text()
    builder.patch_registration = lambda files: instrument_server(production(files), native_main)
    original_relink = builder.relink
    c_commands = []
    selected_inputs = {}
    def inner_relink(build, linked, replacements, archives, server_objects):
        metadata = json.loads((candidate / 'linked/relink.json').read_text())
        root = Path(json.loads((candidate / 'build.json').read_text())['source']) / 'src/external/darlingserver/duct-tape'
        selected = {row['source']: row for row in metadata['inputs']}
        sources = {}
        for key in ('src/task.c', 'src/thread.c', 'src/semaphore.c', 'xnu/osfmk/kern/turnstile.c'):
            # Preserve the exact selected task source, including the builder's
            # saved-EID adoption fix; other bridges come from selected .o peers.
            if key == 'src/task.c':
                path = output / 'task.c'
            else:
                row = selected[key]
                path = Path(row['selected']).with_suffix('') if row['replaced'] else root / key
            sources[key] = path.read_text()
            selected_inputs[str(path)] = digest(path)
        sources = patch_inner_dtape(sources)
        if diagnose_prior:
            # Isolate the active-count regression while retaining the checked
            # allocation changes needed to reach its successful-thread oracle.
            anchor = '\n    dearust_dtape_thread_retire_active(thread);'
            if sources['src/thread.c'].count(anchor) != 2:
                raise ValueError('expected both active-retirement entry points')
            sources['src/thread.c'] = sources['src/thread.c'].replace(anchor, '')
        for key, kind in [('src/task.c', 1), ('src/thread.c', 2), ('src/semaphore.c', 3)]:
            sources[key] = instrument_c(sources[key], kind)
        duct = build / 'src/external/darlingserver/duct-tape'
        flags_file = duct / 'CMakeFiles/darlingserver_duct_tape.dir/flags.make'
        flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines() if ' = ' in line)
        replacements = dict(replacements)
        for key, text in sources.items():
            path = output / 'inner-dtape' / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            command = ['/usr/bin/clang', *sum((shlex.split(flags[k]) for k in ('C_DEFINES', 'C_INCLUDES', 'C_FLAGS')), []),
                       '-c', str(path), '-o', str(path) + '.o', '-MMD', '-MF', str(path) + '.d']
            subprocess.run(command, cwd=duct, check=True)
            c_commands.append(command)
            replacements[key] = Path(str(path) + '.o')
        original_relink(build, linked, replacements, archives, server_objects)
    builder.relink = inner_relink
    sys.argv = [str(Path(builder.__file__).resolve()), '--candidate', str(candidate), '--output', str(output)]
    builder.main()
    for name, expected in selected_inputs.items():
        if digest(Path(name)) != expected:
            raise ValueError('selected C source changed during build: ' + name)
    (output / 'inner-build.json').write_text(json.dumps({'selected_source_sha256': selected_inputs,
                                                       'commands': c_commands}, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--internal-build', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--diagnose-prior-active-count', action='store_true',
                        help='build an owned prior-behavior derivative and require the active-count oracle to fail')
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    output = args.output.resolve()
    if args.internal_build:
        internal_build(candidate, output, args.diagnose_prior_active_count)
        return
    output.mkdir(parents=True, exist_ok=False)
    tools = Path(__file__).resolve().parent
    inputs = [Path(__file__).resolve(), tools / 'patch_prepared_resource_inner_dtape.py',
              tools / 'prepared-resource-inner-dtape-validation.inc',
              tools / 'patch_prepared_resource_actual_cleanup.py',
              tools / 'prepared-resource-actual-cleanup.inc', tools / 'prepared-resource-actual-allocations.inc']
    initial = {str(path): digest(path) for path in inputs}
    base = json.loads((candidate / 'build.json').read_text())
    preserved = {name: digest(Path(name)) for name in base['sha256']}
    command = [sys.executable, str(Path(__file__).resolve()), '--candidate', str(candidate),
               '--output', str(output / 'candidate'), '--internal-build']
    if args.diagnose_prior_active_count:
        command.append('--diagnose-prior-active-count')
    built = subprocess.run(command, text=True, capture_output=True)
    (output / 'build.log').write_text(built.stdout + built.stderr)
    built.check_returncode()
    prefix = output / 'native-prefix'
    prefix.mkdir()
    binary = output / 'candidate/linked/darlingserver'
    result = subprocess.run([str(binary), prefix.name], cwd=output, text=True, capture_output=True,
                            timeout=45, env={**os.environ, 'DSERVER_LOG_LEVEL': 'error'})
    (output / 'native.log').write_text(result.stdout + result.stderr)
    if args.diagnose_prior_active_count:
        expected = 'dying did not retire active ownership exactly once before final C release'
        if result.returncode != 2 or expected not in result.stderr:
            raise ValueError('prior active-count behavior did not produce the expected regression failure')
    else:
        result.check_returncode()
    results = {}
    for line in result.stdout.splitlines():
        for label, marker in [('inner', 'DEARUST_INNER_DTAPE_RESULT '),
                              ('lifecycle', 'DEARUST_ACTUAL_CLEANUP_RESULT ')]:
            if line.startswith(marker): results[label] = json.loads(line[len(marker):])
    if not args.diagnose_prior_active_count and set(results) != {'inner', 'lifecycle'}:
        raise ValueError('missing actual native result')
    for name, expected in {**initial, **preserved}.items():
        if digest(Path(name)) != expected:
            raise ValueError('preserved input changed during validation: ' + name)
    report = dict(scope=__doc__, results=results, command=command,
                  expected_prior_active_count_failure=args.diagnose_prior_active_count,
                  native_returncode=result.returncode,
                  sha256=initial, preserved_candidate_inputs=len(preserved),
                  server=str(binary), server_sha256=digest(binary))
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
