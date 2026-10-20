#!/usr/bin/env python3
"""Build/run experimental default-stack pthread cleanup variants in fresh outputs.

--actual invokes the ordinary actual-dtape validation, without RPC or guests.
Otherwise options are the fresh prepared-resource builder's options.
"""
import hashlib
import json
from pathlib import Path
import sys

import build_prepared_resource_server as builder
from patch_prepared_resource_lane_cost import patch_lane_cost, patch_lane_sustained_validation


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    tools = Path(__file__).resolve().parent
    sources = [Path(__file__).resolve(), tools / 'patch_prepared_resource_lane_cost.py',
               tools / 'prepared-resource-lane-sustained.inc', tools / 'prepared-resource-lane-mutex.c.inc']
    hashes = {str(path): digest(path) for path in sources}
    production = builder.patch_cleanup
    builder.patch_cleanup = lambda files: patch_lane_cost(production(files))
    import patch_prepared_resource_actual_cleanup as actual
    original_actual = actual.patch_actual_cleanup_validation
    actual.patch_actual_cleanup_validation = lambda files, native_main: patch_lane_sustained_validation(
        original_actual(files, native_main))
    if '--actual' in sys.argv or '--internal-build' in sys.argv:
        original_run = builder.subprocess.run

        def compile_test_mutex(command, *args, **kwargs):
            if '-c' in command:
                source = Path(command[command.index('-c') + 1])
                if source.name == 'task.c':
                    text = source.read_text()
                    if 'dearust_lane_mutex_initialize' in text:
                        raise ValueError('test mutex helper already present')
                    source.write_text(text + '\n' + (tools / 'prepared-resource-lane-mutex.c.inc').read_text())
            return original_run(command, *args, **kwargs)

        builder.subprocess.run = compile_test_mutex
    if '--actual' in sys.argv:
        sys.argv.remove('--actual')
        import check_prepared_resource_actual_cleanup as checker
        # Its subprocess must come through this wrapper to retain the transform.
        checker.__file__ = str(Path(__file__).resolve())
        checker.main()
    elif '--internal-build' in sys.argv:
        import check_prepared_resource_actual_cleanup as checker
        checker.__file__ = str(Path(__file__).resolve())
        checker.main()
    else:
        builder.main()
    if any(digest(Path(name)) != expected for name, expected in hashes.items()):
        raise ValueError('cleanup lane cost transform changed while building')
    output = Path(sys.argv[sys.argv.index('--output') + 1]).resolve()
    report = output / ('verification.json' if (output / 'verification.json').exists() else 'build.json')
    record = json.loads(report.read_text())
    record['lane_cost_transform_sha256'] = hashes
    record['native_stack_policy'] = 'unchanged platform-default pthread attributes; no reduced stack bound claimed'
    if report.name == 'verification.json':
        marker = 'DEARUST_ACTUAL_CLEANUP_RESULT '
        results = [json.loads(line[len(marker):]) for line in (output / 'native.log').read_text().splitlines()
                   if line.startswith(marker)]
        if len(results) != 64:
            raise ValueError('sustained cleanup did not complete all 64 rounds')
        record['sustained_actual_validation'] = dict(
            completed_rounds=len(results), concurrent_producers_per_round=4,
            queue_jobs_per_round=4096, total_contended_jobs=4096 * len(results),
            real_task_context_disposals=sum(item['task_context_disposals'] for item in results),
            real_thread_context_disposals=sum(item['thread_context_disposals'] for item in results),
            actual_results=results)
        mutex_results = [int(line.partition(' ')[2])
                         for line in (output / 'native.log').read_text().splitlines()
                         if line.startswith('DEARUST_LANE_MUTEX_RESULT ')]
        if len(mutex_results) != 64 or any(count < 1 for count in mutex_results):
            raise ValueError('real mutex suspension/resume did not complete every round')
        record['forced_dtape_mutex_suspensions'] = mutex_results
    report.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
