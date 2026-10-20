#!/usr/bin/env python3
"""Build a fresh prepared-registration derivative of a frozen resource candidate.

Never overwrites a candidate, reference tree or installed runtime. All server TUs
are rebuilt against the same owned headers; original mixed-language bridges stay.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from patch_prepared_resource_cleanup import patch_cleanup, helper_files
from patch_prepared_resource_publication import patch_publication
from patch_prepared_resource_registration import patch_registration
from patch_prepared_resource_death_barrier import patch_thread_death_barrier
from patch_prepared_resource_checkin import patch_checkin
from relink_darlingserver import relink, validate_output


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    output = args.output.resolve()
    base_path = candidate / 'build.json'
    base = json.loads(base_path.read_text())
    build = Path(base['build']).resolve(strict=True)
    source = Path(base['source']).resolve(strict=True) / 'src/external/darlingserver'
    validate_output(build, output)
    if output.exists() or output.is_relative_to(candidate) or candidate.is_relative_to(output):
        parser.error('output must be absent and disjoint from frozen candidate')
    frozen_hashes = dict(base['sha256'])
    for name, expected in frozen_hashes.items():
        if digest(Path(name)) != expected:
            raise ValueError('frozen input changed: ' + name)
    previous_path = Path(base['server']).parent / 'relink.json'
    previous = json.loads(previous_path.read_text())
    for item in previous['inputs'] + previous['server_inputs']:
        if digest(Path(item['selected'])) != item['selected_sha256']:
            raise ValueError('frozen link input changed: ' + item['selected'])
        frozen_hashes[item['selected']] = item['selected_sha256']
    for item in previous['extra_archives']:
        if digest(Path(item['path'])) != item['sha256']:
            raise ValueError('frozen archive changed: ' + item['path'])
        frozen_hashes[item['path']] = item['sha256']
    tools = Path(__file__).resolve().parent
    transform_inputs = [Path(__file__).resolve(), *helper_files()] + [tools / name for name in (
        'patch_prepared_resource_cleanup.py', 'patch_prepared_resource_publication.py',
        'patch_prepared_resource_registration.py', 'patch_prepared_resource_death_barrier.py',
        'patch_prepared_resource_checkin.py',
        'prepared-resource-cleanup-process.inc', 'prepared-resource-cleanup-thread.inc')]
    direct_inputs = [base_path, previous_path, source / 'duct-tape/src/task.c',
        build / 'src/external/darlingserver/duct-tape/CMakeFiles/darlingserver_duct_tape.dir/flags.make']
    for name in ('process', 'thread', 'server', 'registry'):
        path = candidate / 'internal-include/darlingserver' / (name + '.hpp')
        direct_inputs.append(path if path.is_file() else source / 'internal-include/darlingserver' / (name + '.hpp'))
    direct_inputs += [candidate / 'src' / (name + '.cpp') for name in ('process', 'thread', 'server', 'call')]
    initial_hashes = dict(frozen_hashes)
    initial_hashes.update({str(p): digest(p) for p in transform_inputs + direct_inputs})
    shutil.copytree(candidate, output, ignore=shutil.ignore_patterns('*.o', '*.a', 'linked', 'build.json'))
    for filename in ('resource-policy-host.o', 'libresource-policy.a'):
        shutil.copyfile(candidate / filename, output / filename)
    files = {}
    for name in ('process', 'thread', 'server', 'registry'):
        path = candidate / 'internal-include/darlingserver' / (name + '.hpp')
        if not path.is_file():
            path = source / 'internal-include/darlingserver' / (name + '.hpp')
        files[name + '.hpp'] = path.read_text()
    for name in ('process', 'thread', 'server', 'call'):
        files[name + '.cpp'] = (candidate / 'src' / (name + '.cpp')).read_text()
    files = patch_checkin(patch_thread_death_barrier(patch_registration(patch_publication(patch_cleanup(files)))))
    # An actual C-side check, including kernel-task adoption, prevents a later
    # refactor silently reverting to an initialized but incorrect saved identity.
    files['process.cpp'] = '#include <new>\nextern "C" unsigned long long dearust_prepared_task_saved_eid(void*);\n' + files['process.cpp']
    files['process.cpp'] = files['process.cpp'].replace(
        'if (!_dtapeTask) throw std::bad_alloc();',
        'if (!_dtapeTask) throw std::bad_alloc();\n'
        '    if (dearust_prepared_task_saved_eid(_dtapeTask) != _eid) std::terminate();')
    for name, text in files.items():
        path = output / ('internal-include/darlingserver' if name.endswith('.hpp') else 'src') / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for path in helper_files():
        shutil.copyfile(path, output / 'tools' / path.name)
    commands = []
    server_objects = {}
    for key, value in base['server_objects'].items():
        original = str(Path(value).with_suffix(''))
        command = next(c for c in base['commands'] if '-c' in c and original in c)
        command = [part.replace(str(candidate), str(output)) for part in command]
        depfile = str(output / (key + '.d'))
        command += ['-MMD', '-MF', depfile]
        subprocess.run(command, cwd=build / 'src/external/darlingserver', check=True)
        commands.append(command)
        server_objects[key] = output / (key + '.o')
    # Kernel adoption must update the saved C identity as well as its context.
    task_original = source / 'duct-tape/src/task.c'
    text = task_original.read_text()
    anchor = '\t\t\ttask->context = context;'
    if text.count(anchor) != 1:
        raise ValueError('kernel task adoption anchor not unique')
    text = text.replace(anchor, anchor + '\n\t\t\ttask->p_ident.eid = dtape_hooks->task_eternal_id(context);', 1)
    text += '\nunsigned long long dearust_prepared_task_saved_eid(void* raw) {\n'
    text += '    return ((dtape_task_t*)raw)->p_ident.eid;\n}\n'
    task = output / 'task.c'
    task.write_text(text)
    duct = build / 'src/external/darlingserver/duct-tape'
    flags_path = duct / 'CMakeFiles/darlingserver_duct_tape.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_path.read_text().splitlines() if ' = ' in line)
    command = ['/usr/bin/clang', *sum((shlex.split(flags[k]) for k in ('C_DEFINES', 'C_INCLUDES', 'C_FLAGS')), []),
               '-c', str(task), '-o', str(task) + '.o', '-MMD', '-MF', str(task) + '.d']
    subprocess.run(command, cwd=duct, check=True)
    commands.append(command)
    replacements = {r['source']: Path(r['selected']) for r in previous['inputs'] if r['replaced']}
    replacements['src/task.c'] = Path(str(task) + '.o')
    archives = [output / 'libresource-policy.a' if Path(r['path']).name == 'libresource-policy.a' else Path(r['path'])
                for r in previous['extra_archives']]
    relink(build, output / 'linked', replacements, archives, server_objects)
    for name, expected in initial_hashes.items():
        if digest(Path(name)) != expected:
            raise ValueError('build input changed during compilation: ' + name)
    inputs = [Path(name) for name in initial_hashes]
    inputs += [p for p in output.rglob('*') if p.is_file() and p.suffix in {'.cpp', '.hpp', '.c', '.inc', '.h', '.o', '.a', '.d'}]
    record = dict(scope='Owned prepared-registration candidate; resource ownership/enforcement unactivated; inner XNU OOM/startup/performance gates remain open.',
        inputs_unchanged=True, initial_input_sha256=initial_hashes,
        parent_candidate=str(candidate), parent_report_sha256=digest(base_path), build=str(build), source=str(source),
        commands=commands, sha256={str(p): digest(p) for p in inputs},
        server=str(output / 'linked/darlingserver'), server_sha256=digest(output / 'linked/darlingserver'),
        server_objects={key: str(value) for key, value in server_objects.items()},
        kernel_saved_identity_checked=True, resource_ownership_activated=False)
    (output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({k: record[k] for k in ('scope', 'server', 'server_sha256')}, indent=2))


if __name__ == '__main__':
    main()
