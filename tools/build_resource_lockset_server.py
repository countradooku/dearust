#!/usr/bin/env python3
"""Build the current Rust lockset and its C boundary into an owned lifecycle server.

The frozen preceding candidate is only read. Both its archive and all earlier
selected C bridges remain preserved; the new inputs are explicitly recorded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--work-queue', required=True, choices=['single', 'multi'])
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    candidate = args.candidate.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(candidate) or candidate.is_relative_to(output):
        parser.error('output must be absent and disjoint from the preceding candidate')
    output.mkdir(parents=True)
    sources = [repo/'Cargo.toml', repo/'Cargo.lock', Path(__file__).resolve(),
               *sorted((repo/'crates/dearust-lockset').rglob('*.rs')),
               repo/'crates/dearust-lockset/Cargo.toml',
               repo/'crates/dearust-lockset/bridge/sync_lock.c']
    hashes = {str(path): digest(path) for path in sources}
    command = ['cargo', 'build', '--locked', '-p', 'dearust-lockset', '--release',
               '--target-dir', str(output/'rust-target')]
    subprocess.run(command, cwd=repo, check=True)
    archive = output/'rust-target/release/libdearust_lockset.a'
    if any(digest(Path(path)) != value for path, value in hashes.items()):
        raise ValueError('lockset input changed while Rust was compiling')
    record = dict(scope=__doc__, command=command, source_sha256=hashes,
                  archive=str(archive), archive_sha256=digest(archive),
                  rustc=subprocess.check_output(['rustc', '-vV'], text=True),
                  resource_ownership_activated=False)
    rust_report = output/'rust-build.json'
    rust_report.write_text(json.dumps(record, indent=2)+'\n')

    import build_resource_lifecycle_server as lifecycle
    import build_resource_workqueue_configuration as configuration
    original_configure = lifecycle.configure
    bridge = repo/'crates/dearust-lockset/bridge/sync_lock.c'
    bridge_key = 'xnu/osfmk/kern/sync_lock.c'

    def configure(builder, selected, server_output, **keywords):
        original_selected = builder.selected_duct_source

        def select(previous, source, key):
            return bridge if key == bridge_key else original_selected(previous, source, key)

        builder.selected_duct_source = select
        try:
            original_configure(builder, selected, server_output,
                               extra_c=(bridge_key,), **keywords)
        finally:
            builder.selected_duct_source = original_selected
        builder.EXTRA_TRANSFORM_INPUTS += [*sources, archive, rust_report]
        builder.EXTRA_BUILD_RECORDS.append('lockset-configuration.json')
        previous_relink = builder.relink

        def relink(build, linked, replacements, archives, server_objects):
            matches = [path for path in archives if Path(path).name in
                       ('libdarling_lockset.a', 'libdearust_lockset.a')]
            if len(matches) != 1:
                raise ValueError('expected exactly one preceding Rust lockset archive')
            archives = [archive if path == matches[0] else path for path in archives]
            previous_relink(build, linked, replacements, archives, server_objects)
            report = dict(record, preceding_archive=str(matches[0]),
                          preceding_archive_sha256=digest(Path(matches[0])),
                          selected_bridge=str(bridge), selected_bridge_sha256=digest(bridge))
            (server_output/'lockset-configuration.json').write_text(json.dumps(report, indent=2)+'\n')

        builder.relink = relink

    lifecycle.configure = configure
    sys.argv = ['build_resource_workqueue_configuration', '--candidate', str(candidate),
                '--output', str(output/'server'), '--work-queue', args.work_queue]
    try:
        configuration.main()
        if any(digest(Path(path)) != value for path, value in hashes.items()):
            raise ValueError('lockset input changed between Rust and server compilation')
        if digest(archive) != record['archive_sha256']:
            raise ValueError('selected Rust archive changed during server compilation')
    finally:
        lifecycle.configure = original_configure


if __name__ == '__main__':
    main()
