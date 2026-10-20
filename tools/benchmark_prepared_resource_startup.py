#!/usr/bin/env python3
"""Compare owned native startup through kernel initialization, without any RPC."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time

from relink_darlingserver import relink, validate_output


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


MEASUREMENT = r'''
extern struct timespec dearust_startup_begin;
static void dearust_startup_measure() {
    struct timespec end;
    struct rusage usage;
    if (clock_gettime(CLOCK_MONOTONIC, &end) || getrusage(RUSAGE_SELF, &usage)) std::abort();
    long rss = -1, threads = -1, virtualSize = -1, data = -1;
    auto* status = fopen("/proc/self/status", "r");
    if (!status) std::abort();
    char line[256];
    while (fgets(line, sizeof(line), status)) {
        long value;
        if (sscanf(line, "VmRSS: %ld kB", &value) == 1) rss = value;
        if (sscanf(line, "Threads: %ld", &value) == 1) threads = value;
        if (sscanf(line, "VmSize: %ld kB", &value) == 1) virtualSize = value;
        if (sscanf(line, "VmData: %ld kB", &value) == 1) data = value;
    }
    fclose(status);
    const long long elapsed = (end.tv_sec - dearust_startup_begin.tv_sec) * 1000000000LL
        + end.tv_nsec - dearust_startup_begin.tv_nsec;
    const long long cpu = (usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000000LL
        + usage.ru_utime.tv_usec + usage.ru_stime.tv_usec;
    printf("DEARUST_STARTUP {\"wall_ns\":%lld,\"cpu_us\":%lld,\"maxrss_kib\":%ld,\"rss_kib\":%ld,\"vmsize_kib\":%ld,\"vmdata_kib\":%ld,\"threads\":%ld}\n",
        elapsed, cpu, usage.ru_maxrss, rss, virtualSize, data, threads);
    fflush(stdout);
    _Exit(0);
}
'''


def variant(candidate, output, prepared):
    candidate = candidate.resolve(strict=True)
    base = json.loads((candidate / 'build.json').read_text())
    build = Path(base['build'])
    validate_output(build, output)
    previous = json.loads((candidate / 'linked/relink.json').read_text())
    hashes = {str(candidate / 'build.json'): digest(candidate / 'build.json')}
    historical_drift = []
    for name, expected in base['sha256'].items():
        path = Path(name)
        observed = digest(path)
        if observed != expected:
            if path.is_relative_to(candidate):
                raise ValueError('owned candidate input changed: ' + name)
            historical_drift.append(dict(path=name, recorded=expected, observed=observed))
        hashes[name] = observed
    for item in previous['inputs'] + previous['server_inputs']:
        hashes[item['selected']] = item['selected_sha256']
    for item in previous['extra_archives']:
        hashes[item['path']] = item['sha256']
    if any(digest(Path(name)) != expected for name, expected in hashes.items()):
        raise ValueError('candidate inputs changed')
    output.mkdir(parents=True, exist_ok=False)
    main = (candidate / 'src/darlingserver.cpp').read_text()
    begin = main.index('int main(int argc, char** argv) {')
    main = '#include <time.h>\n' + main[:begin] + '''
struct timespec dearust_startup_begin;
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    if (clock_gettime(CLOCK_MONOTONIC, &dearust_startup_begin)) return 2;
    signal(SIGPIPE, SIG_IGN);
    DarlingServer::Message::initializeServerCredentials();
    auto* server = new DarlingServer::Server(argv[1]);
    server->start();
    return 2;
}
'''
    server = (candidate / 'src/server.cpp').read_text()
    anchor = '\tThread::initializeResourceCleanup();' if prepared else '\tThread::kernelSync(dtape_init_in_thread);'
    if server.count(anchor) != 1:
        raise ValueError('startup measurement anchor not unique')
    server = '#include <cstdio>\n#include <cstdlib>\n#include <sys/resource.h>\n#include <time.h>\n' + MEASUREMENT + server.replace(anchor, anchor + '\n\tdearust_startup_measure();', 1)
    objects = {item['source']: Path(item['selected']) for item in previous['server_inputs'] if item['source']}
    commands = []
    for name, text in [('darlingserver', main), ('server', server)]:
        source = output / (name + '.cpp')
        source.write_text(text)
        original = str(candidate / 'src' / (name + '.cpp'))
        command = list(next(command for command in base['commands'] if '-c' in command and original in command))
        command[command.index('-c') + 1] = str(source)
        obj = Path(str(source) + '.o')
        command[command.index('-o') + 1] = str(obj)
        # Do not overwrite a derivative's existing dependency record.
        if '-MF' in command:
            command[command.index('-MF') + 1] = str(source) + '.d'
        subprocess.run(command, cwd=build / 'src/external/darlingserver', check=True,
                       capture_output=True, text=True)
        commands.append(command)
        objects['src/' + name + '.cpp'] = obj
    replacements = {item['source']: Path(item['selected']) for item in previous['inputs'] if item['replaced']}
    relink(build, output / 'linked', replacements,
           [Path(item['path']) for item in previous['extra_archives']], objects)
    return dict(binary=str(output / 'linked/darlingserver'), commands=commands,
                preserved_sha256=hashes, historical_noncompiled_input_drift=historical_drift,
                generated_source_sha256={str(path): digest(path) for path in output.glob('*.cpp')},
                binary_sha256=digest(output / 'linked/darlingserver'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--samples', type=int, default=20)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tool = Path(__file__).resolve()
    tool_hash = digest(tool)
    variants = {
        'baseline': variant(args.baseline, output / 'baseline', False),
        'prepared': variant(args.candidate, output / 'prepared', True),
    }
    records = []
    for index in range(args.samples):
        for name in (('baseline', 'prepared') if index % 2 == 0 else ('prepared', 'baseline')):
            prefix = output / ('prefix-' + str(index) + '-' + name)
            prefix.mkdir()
            started = time.monotonic_ns()
            # A relative owned prefix avoids Linux's sockaddr_un path limit.
            result = subprocess.run([variants[name]['binary'], prefix.name], cwd=output,
                                    capture_output=True, text=True, timeout=30)
            elapsed = time.monotonic_ns() - started
            (output / ('sample-' + str(index) + '-' + name + '.log')).write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError('startup harness failed: ' + name + ': ' + result.stderr[-2000:])
            line = next(line for line in result.stdout.splitlines() if line.startswith('DEARUST_STARTUP '))
            record = json.loads(line.partition(' ')[2])
            records.append(dict(variant=name, index=index, process_wall_ns=elapsed, **record))
    unchanged = digest(tool) == tool_hash and all(
        digest(Path(path)) == expected for item in variants.values()
        for path, expected in item['preserved_sha256'].items())
    summary = {name: {key: statistics.median(record[key] for record in records if record['variant'] == name)
                     for key in ('wall_ns', 'cpu_us', 'rss_kib', 'vmsize_kib', 'vmdata_kib', 'threads', 'process_wall_ns')}
               for name in variants}
    report = dict(passed=unchanged, inputs_unchanged=unchanged, variants=variants,
        tool_sha256={str(tool): tool_hash},
        samples=records, medians=summary,
        scope='Fresh owned native Server startup through dtape kernel initialization; prepared includes '
              'cleanup lane readiness. No RPC, guest workload, steady-state performance or acceptance claim. '
              'RSS includes loader/library residency; kernel microthread stacks may be largely unresident. '
              'Raw ru_maxrss may retain pre-exec inherited high water and is excluded from summaries.')
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=unchanged, medians=summary), indent=2))
    if not unchanged:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
