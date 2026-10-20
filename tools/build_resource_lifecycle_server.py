#!/usr/bin/env python3
"""Build an owned resource lifecycle derivative, preserving selected C bridges.

The default prepared builder remains usable for its earlier focused validators.
This derivative composes the further lifecycle/notification/inner-C fixes and
declares every extra source before the builder freezes inputs.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

from patch_prepared_resource_lane_cost import patch_lane_cost
from patch_prepared_resource_inner_dtape import patch_inner_dtape
from patch_prepared_resource_process_notifications import patch_process_notifications
from patch_prepared_resource_machport_retirement import patch_machport_retirement, patch_machport_c
from patch_prepared_resource_kq_ownership import patch_kq_ownership
from patch_prepared_resource_mach_notifications import patch_mach_notifications
from patch_prepared_resource_mach_waiter import patch_mach_waiter
from patch_resource_state_death import patch_resource_state_death
from patch_resource_death_work import patch_death_work
from patch_resource_death_lifecycle import patch_death_lifecycle, patch_task_dispose_identity
from patch_prepared_resource_checked_ipc import patch_checked_cpp, patch_checked_c
from patch_resource_kernel_contexts import patch_kernel_context_cpp, patch_kernel_context_c
from patch_resource_prepared_exec import patch_prepared_exec
from patch_prepared_resource_replies import patch_prepared_replies
from patch_resource_generation_resume import patch_generation_resume
from patch_resource_worker_ownership import patch_worker_ownership_files
from patch_resource_work_handoff import patch_work_handoff


def configure(builder, candidate, output, instrument_c=None, extra_c=(), extra_c_flags=(), checked_generation=False, prepared_exec=False):
    if prepared_exec and not checked_generation:
        raise ValueError('prepared exec requires checked acquisitions and generation contexts')
    base = json.loads((candidate / 'build.json').read_text())
    source = Path(base['source']) / 'src/external/darlingserver'
    previous = json.loads((candidate / 'linked/relink.json').read_text())
    tools = Path(__file__).resolve().parent
    builder.EXTRA_BUILD_RECORDS = ['lifecycle-c-build.json']
    builder.EXTRA_SERVER_INPUTS = {
        'workers.hpp': source / 'internal-include/darlingserver/workers.hpp',
        'stack-pool.cpp': candidate / 'src/stack-pool.cpp',
        'kqchan.hpp': source / 'internal-include/darlingserver/kqchan.hpp',
        'kqchan.cpp': candidate / 'src/kqchan.cpp',
        'resource-server-state.inc': candidate / 'tools/resource-server-state.inc',
        'resource-cpu-timer.inc': candidate / 'tools/resource-cpu-timer.inc',
    }
    checked_extras = ('xnu/osfmk/kern/ipc_tt.c', 'xnu/osfmk/kern/ipc_kobject.c', 'pthread/kern_synch.c') if checked_generation else ()
    c_keys = tuple(dict.fromkeys(('src/task.c', 'src/thread.c', 'xnu/osfmk/kern/turnstile.c', 'src/kqchan.c', *checked_extras, *extra_c)))
    c_inputs = {key: builder.selected_duct_source(previous, source, key) for key in c_keys}
    builder.EXTRA_TRANSFORM_INPUTS = [Path(__file__).resolve(), *c_inputs.values(), *[
        tools / name for name in (
            'patch_prepared_resource_lane_cost.py', 'patch_prepared_resource_inner_dtape.py',
            'patch_prepared_resource_process_notifications.py', 'prepared-resource-process-notifications.inc',
            'patch_prepared_resource_machport_retirement.py', 'patch_resource_state_death.py',
            'patch_prepared_resource_kq_ownership.py', 'patch_prepared_resource_mach_notifications.py',
            'prepared-resource-mach-notifications.inc', 'patch_prepared_resource_mach_waiter.py',
            'patch_resource_death_work.py', 'patch_resource_worker_ownership.py',
            'patch_resource_death_lifecycle.py', 'patch_resource_work_handoff.py')]]
    if checked_generation:
        builder.EXTRA_TRANSFORM_INPUTS += [tools / 'patch_prepared_resource_checked_ipc.py',
            tools / 'patch_resource_kernel_contexts.py', tools / 'patch_resource_generation_resume.py',
            c_inputs['pthread/kern_synch.c'].parent / 'kern_internal.h']
    if prepared_exec:
        builder.EXTRA_SERVER_INPUTS.update({
            'message.hpp': source / 'internal-include/darlingserver/message.hpp',
            'message.cpp': candidate / 'src/message.cpp',
            'rpc.internal.h': candidate / 'internal-include/darlingserver/rpc.internal.h',
        })
        builder.EXTRA_TRANSFORM_INPUTS += [tools / name for name in (
            'patch_resource_prepared_exec.py', 'resource-prepared-exec.inc',
            'patch_prepared_resource_replies.py', 'prepared-resource-reply-queue.inc',
            'prepared-resource-reply-send.inc')]
    cleanup = builder.patch_cleanup
    builder.patch_cleanup = lambda files: patch_lane_cost(cleanup(files))
    checkin = builder.patch_checkin

    def lifecycle(files):
        files = checkin(files)
        files = patch_death_work(files)
        files = patch_worker_ownership_files(files)
        files = patch_resource_state_death(files)
        files = patch_process_notifications(files)
        files = patch_machport_retirement(files)
        files = patch_death_lifecycle(files)
        files = patch_mach_notifications(patch_kq_ownership(files))
        if checked_generation:
            files = patch_generation_resume(patch_kernel_context_cpp(patch_checked_cpp(files)))
        if prepared_exec:
            files = patch_prepared_replies(patch_prepared_exec(files))
        return patch_work_handoff(files)

    builder.patch_checkin = lifecycle
    previous_relink = builder.relink
    flags_path = Path(base['build']) / 'src/external/darlingserver/duct-tape/CMakeFiles/darlingserver_duct_tape.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_path.read_text().splitlines() if ' = ' in line)
    c_flags = sum((shlex.split(flags[key]) for key in ('C_DEFINES', 'C_INCLUDES', 'C_FLAGS')), [])

    def lifecycle_relink(build, linked, replacements, archives, server_objects):
        sources = {key: path.read_text() for key, path in c_inputs.items()}
        # Preserve the saved-EID adoption change already generated by the base
        # builder from the selected lockset bridge, before further C transforms.
        sources['src/task.c'] = (output / 'task.c').read_text()
        sources = patch_inner_dtape(sources)
        sources['src/task.c'] = patch_task_dispose_identity(sources['src/task.c'])
        sources['src/kqchan.c'] = patch_machport_c(sources['src/kqchan.c'])
        sources['src/kqchan.c'] = patch_mach_waiter(sources['src/kqchan.c'])
        if checked_generation:
            sources = patch_kernel_context_c(patch_checked_c(sources))
        if instrument_c:
            sources = instrument_c(sources)
        commands = []
        replacements = dict(replacements)
        for key, text in sources.items():
            path = output / 'duct-tape' / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            command = ['/usr/bin/clang', *c_flags, *extra_c_flags, '-I', str(c_inputs[key].parent), '-c', str(path), '-o', str(path)+'.o',
                       '-MMD', '-MF', str(path)+'.d']
            subprocess.run(command, cwd=Path(base['build'])/'src/external/darlingserver/duct-tape', check=True)
            commands.append(command)
            replacements[key] = Path(str(path)+'.o')
        previous_relink(build, linked, replacements, archives, server_objects)
        # The outer builder hashes this report and generated C inputs. Its
        # initial snapshot also covers all selected adjacent bridge sources.
        (output / 'lifecycle-c-build.json').write_text(json.dumps(dict(
            commands=commands, selected_sources={key: str(path) for key, path in c_inputs.items()},
            compiled_sources={key: str(output/'duct-tape'/key) for key in sources}), indent=2)+'\n')

    builder.relink = lifecycle_relink


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    candidate, output = args.candidate.resolve(strict=True), args.output.resolve()
    import build_prepared_resource_server as builder
    configure(builder, candidate, output, checked_generation=True, prepared_exec=True)
    sys.argv = [str(Path(builder.__file__).resolve()), '--candidate', str(candidate), '--output', str(output)]
    builder.main()


if __name__ == '__main__':
    main()
