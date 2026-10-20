#!/usr/bin/env python3
"""Reconcile pinned inventories, captured graph, manifests and scoped evidence.

No reference working files, clock, network or implicit HEAD is consulted.
Hash-valid historical evidence is not a claim of a fresh execution.
"""
import argparse
from collections import Counter, defaultdict
from collections import deque
import hashlib
import json
from pathlib import Path
import tomllib


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique(rows, key, label):
    result = {}
    for row in rows:
        identity = key(row)
        if identity in result:
            raise ValueError(f'duplicate {label}: {identity}')
        result[identity] = row
    return result


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=pairs)


def manifests(root):
    artifacts = []
    for flavor in ('host', 'runtime'):
        path = root / f'xtask/{flavor}-artifacts.tsv'
        for line in path.read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            fields = line.split('\t')
            if flavor == 'runtime':
                kind, target, package, features, visibility, destination, definition = fields
            else:
                kind, target, package, destination, definition, *_ = fields
                features, visibility = 'manifest-default', 'manifest-default'
            artifacts.append(dict(id=f'{flavor}:{destination}', kind=kind, target=target,
                                  package=package, features=features, visibility=visibility,
                                  destination=destination, definition=definition,
                                  staging=flavor, manifest=str(path.relative_to(root))))
    unique(artifacts, lambda a: a['id'], 'manifest artifact')
    return artifacts


def import_v1(legacy):
    """Preserve v1 as historical annotations; never infer new verification stages."""
    if legacy['schema_version'] != 1:
        raise ValueError('expected schema v1 import')
    unique(legacy['targets'], lambda t: t['id'], 'v1 target')
    if legacy['counts']['observed_default_targets'] != len(legacy['targets']):
        raise ValueError('v1 target count mismatch')
    return dict(schema_version=1, upstream_commit=legacy['upstream_commit'], counts=legacy['counts'],
                scope='Historical v1 annotations only, not current status; no new link/execution/activation claim inferred.',
                source_scope=legacy.get('scope'), additional_optional_artifacts=legacy.get('additional_optional_artifacts', []),
                target_annotations=[t for t in legacy['targets'] if t['status'] != 'pending'
                    or 'verified_source_slices' in t or 'legacy_consumer_integration' in t])


def inventory_directories(leaves):
    directories = {''}
    for path in leaves:
        parent = path.rpartition('/')[0]
        while parent not in directories:
            directories.add(parent)
            parent = parent.rpartition('/')[0]
    return directories


def resolve_source(path, leaves, directories=None):
    """Resolve only committed relative symlink blobs, never host/SDK paths."""
    pending, parts, trace = deque(path.split('/')), [], []
    if directories is None:
        directories = inventory_directories(leaves)
    if path.startswith(('/', '$')):
        return path, trace, 'external-root'
    while pending:
        part = pending.popleft()
        if part in ('', '.'):
            continue
        if part == '..':
            if not parts:
                return path, trace, 'escapes-inventory-root'
            parts.pop()
            continue
        parts.append(part)
        candidate = '/'.join(parts)
        entry = leaves.get(candidate)
        if entry and entry.get('mode') == '120000':
            target = entry.get('symlink_target')
            trace.append(dict(path=candidate, target=target, commit=entry.get('commit'), object_id=entry['object_id']))
            if target is None:
                return path, trace, 'symlink-target-not-captured'
            if target.startswith('/'):
                return path, trace, 'absolute-sdk-or-host-symlink'
            if len(trace) > 64:
                return path, trace, 'symlink-cycle-or-depth-unresolved'
            parts.pop()
            pending.extendleft(reversed(target.split('/')))
        elif pending and candidate not in directories:
            return path, trace, 'traverses-missing-directory-or-file'
    canonical = '/'.join(parts)
    return canonical, trace, 'resolved' if canonical in leaves else 'unresolved-alias-or-external-input'


def reconcile(root, graph, superproject, dependencies, ledger):
    if ledger['schema_version'] != 2:
        raise ValueError('unsupported ledger schema')
    pin = superproject['upstream_commit']
    if pin != dependencies['upstream_commit'] or pin != ledger['upstream_commit']:
        raise ValueError('inconsistent upstream pins')
    leaves = {}
    for prefix, commit, entries in [('', pin, superproject['entries'])] + [
            (d['path'] + '/', d['commit'], d.get('entries', [])) for d in dependencies['dependencies']]:
        for entry in entries:
            path = prefix + entry['path']
            if path in leaves:
                raise ValueError(f'duplicate upstream entry: {path}')
            leaves[path] = dict(entry, path=path, commit=commit)
    unique(dependencies['dependencies'], lambda d: d['path'], 'dependency')
    observed = unique([dict(t, graph_reference=ref) for ref, t in graph['objects'].items()
                       if 'type' in t], lambda t: t['id'], 'graph target')
    artifacts = unique(manifests(root) + ledger.get('extra_artifacts', []),
                       lambda a: a['id'], 'artifact')
    packages = []
    for filename in ledger['cargo_manifests']:
        data = tomllib.loads((root / filename).read_text())
        packages.append(dict(path=filename, name=data['package']['name'],
                             features=data.get('features', {}), lib=data.get('lib', {})))
    package_names = unique(packages, lambda p: p['name'], 'Cargo package')
    for artifact in artifacts.values():
        if artifact['package'] not in package_names:
            raise ValueError(f'missing package manifest: {artifact["package"]}')
        if artifact['kind'] not in {'bin', 'object', 'staticlib'}:
            raise ValueError('unknown artifact kind')
        if artifact.get('staging') == 'runtime':
            selected = set(artifact['features'].split(',')) - {'-'}
            if selected - package_names[artifact['package']]['features'].keys():
                raise ValueError('unknown runtime feature')
    evidence = unique(ledger['evidence'], lambda e: e['id'], 'evidence')
    evidence_index = []
    for record in evidence.values():
        path = root / record['path']
        state = 'missing' if not path.is_file() else ('hash-matched' if digest(path) == record['sha256'] else 'stale')
        if state == 'hash-matched' and record.get('assertions'):
            report = load(path)
            for assertion in record['assertions']:
                value = report
                try:
                    for key in assertion['pointer'].split('/')[1:]:
                        key = key.replace('~1', '/').replace('~0', '~')
                        value = value[int(key)] if isinstance(value, list) else value[key]
                    if type(value) is not type(assertion['equals']) or value != assertion['equals']:
                        state = 'contradictory'
                except (KeyError, IndexError, TypeError, ValueError):
                    state = 'contradictory'
        evidence_index.append(dict(record, availability=state))
    evidence_states = {e['id']: e['availability'] for e in evidence_index}
    sources = unique(ledger['source_ports'], lambda s: s['source'], 'source port')
    for source, port in sources.items():
        if source not in leaves or leaves[source]['object_id'] != port['object_id'] or leaves[source]['commit'] != port['commit']:
            raise ValueError(f'source pin/blob mismatch: {source}')
        if leaves[source]['category'] != 'source':
            raise ValueError(f'non-implementation source port: {source}')
        if port.get('legacy_status', leaves[source]['status']) != leaves[source]['status']:
            raise ValueError('source status disagrees with legacy inventory')
        if not port['artifacts'] or not port['rust_sources']:
            raise ValueError('source port requires implementation and artifact')
        for artifact in port['artifacts']:
            if artifact not in artifacts:
                raise ValueError(f'missing artifact: {artifact}')
        for path in port['rust_sources']:
            if (not (root / path).is_file() or not path.endswith('.rs')
                    or {'tests', 'fixtures', 'benches'} & set(Path(path).parts)):
                raise ValueError(f'missing Rust implementation: {path}')
    annotated = {p for p, e in leaves.items() if e['status'] != 'pending'}
    if annotated != sources.keys():
        raise ValueError('source ledger and legacy inventory annotations disagree')
    participation = defaultdict(list)
    generated = defaultdict(list)
    literal_paths = set()
    directories = inventory_directories(leaves)
    source_records = 0
    for target in observed.values():
        for index, source in enumerate(target.get('sources', [])):
            source_records += 1
            path = source['path'].removeprefix('$SOURCE/')
            if source.get('isGenerated'):
                generated[path].append(dict(target=target['id'], source_index=index, has_compile_group='compileGroupIndex' in source))
            else:
                literal_paths.add(path)
                canonical, trace, state = resolve_source(path, leaves, directories)
                participation[canonical].append(dict(target=target['id'], source_index=index,
                    has_compile_group='compileGroupIndex' in source, graph_path=path, symlink_trace=trace, resolution=state))
    bindings = unique(ledger['bindings'], lambda b: (b['target'], b['artifact']), 'target artifact binding')
    target_bindings = defaultdict(list)
    optional = []
    for binding in bindings.values():
        if any(type(binding[k]) is not bool for k in ('compiled', 'linked', 'executed', 'production_active')):
            raise ValueError('verification flags must be booleans')
        if binding['artifact'] not in artifacts:
            raise ValueError(f'missing artifact: {binding["artifact"]}')
        if not binding['scope'] or not binding['architectures'] or not binding['configurations']:
            raise ValueError('binding needs explicit scope/architectures/configurations')
        if binding['executed'] and not binding['linked']:
            raise ValueError('execution without consumer link')
        if binding['linked'] and not binding['compiled']:
            raise ValueError('link without compilation')
        if binding['activation'] == 'candidate-only' and binding.get('production_active'):
            raise ValueError('candidate cannot be production active')
        if binding['production_active'] and not (binding['compiled'] and binding['linked'] and binding['executed']):
            raise ValueError('production activation requires compilation, link and execution')
        if any(e not in evidence for e in binding['evidence']):
            raise ValueError('unknown evidence reference')
        if any(binding[k] for k in ('compiled', 'linked', 'executed')) and not binding['evidence']:
            raise ValueError('verified claim needs evidence')
        valid = bool(binding['evidence']) and all(evidence_states[e] == 'hash-matched' for e in binding['evidence'])
        item = dict(binding, evidence_currently_available=valid)
        matches = [t for t in observed.values() if t['name'] == binding['target']]
        if not matches:
            if not binding.get('optional'):
                raise ValueError(f'required target absent: {binding["target"]}')
            optional.append(item)
        for target in matches:
            target_bindings[target['id']].append(item)
    retirements = unique(ledger.get('retirements', []), lambda r: r['target'], 'retirement')
    replacements = unique(ledger.get('whole_target_replacements', []), lambda r: r['target'], 'replacement')
    if (set(retirements) | set(replacements)) - {t['name'] for t in observed.values()}:
        raise ValueError('retirement target absent from captured graph')
    entries = []
    for target in sorted(observed.values(), key=lambda t: (t['name'], t['id'])):
        rows = target_bindings[target['id']]
        ports = [dict(port) for path, port in sources.items()
                 if any(p['target'] == target['id'] for p in participation.get(path, []))]
        retirement = retirements.get(target['name'])
        completion = replacements.get(target['name']) or (retirement if retirement and retirement['retired'] else None)
        complete = bool(completion and completion.get('verified', completion.get('retired', False)))
        if complete:
            required = ('all_consumers_verified', 'all_configurations_verified', 'all_architectures_verified',
                        'semantics_passed', 'performance_passed')
            incomplete_inputs = [p for p, uses in participation.items()
                if any(u['target'] == target['id'] and u['has_compile_group'] for u in uses)
                and p not in sources]
            if any(u['target'] == target['id'] for uses in generated.values() for u in uses) and not completion.get('generated_inputs_verified'):
                incomplete_inputs.append('unverified generated input closure')
            if (not all(completion.get(k) is True for k in required) or not rows or incomplete_inputs
                    or any(p.get('open_gates') for p in ports) or any(
                    not (b['compiled'] and b['linked'] and b['executed'] and b.get('production_active')
                         and b['evidence_currently_available']) or b['open_gates']
                    or b.get('original_slices_retained') for b in rows)):
                raise ValueError('retirement requires complete supported scope and activation')
        if retirement and retirement['retired'] and (not complete or not retirement.get('legacy_build_removed')):
            raise ValueError('retirement requires verified replacement and legacy build removal')
        entries.append(dict(name=target['name'], id=target['id'], type=target['type'],
                            graph_reference=target['graph_reference'], status='retired' if retirement and retirement['retired'] else 'replacement-verified' if complete else 'pending',
                            legacy_target_retired=bool(retirement and retirement['retired']),
                            source_ports=ports, artifact_bindings=rows,
                            whole_target_replacement=complete,
                            coverage='scoped-partial' if ports or rows else 'unmapped',
                            original_architecture_slices_retained=sorted({a for b in rows for a in b.get('original_slices_retained', [])})))
    counts = dict(observed_default_targets=len(entries),
                  with_cargo_artifact_slice=sum(bool(t['artifact_bindings']) for t in entries),
                  with_source_port_participation=sum(bool(t['source_ports']) for t in entries),
                  with_rust_work=sum(bool(t['artifact_bindings'] or t['source_ports']) for t in entries),
                  with_consumer_link_evidence=sum(any(b['linked'] and b['evidence_currently_available'] for b in t['artifact_bindings']) for t in entries),
                  with_execution_evidence=sum(any(b['executed'] and b['evidence_currently_available'] for b in t['artifact_bindings']) for t in entries),
                  with_compiled_unlinked_scopes=sum(any(b['compiled'] and not b['linked'] and b['evidence_currently_available'] for b in t['artifact_bindings']) for t in entries),
                  production_active_targets=sum(any(b.get('production_active') and b['evidence_currently_available'] for b in t['artifact_bindings']) for t in entries),
                  unique_ported_upstream_sources=len(sources),
                  unique_sources_with_rust_work=len(sources),
                  unique_sources_participating_in_captured_graph=len({p['source'] for t in entries for p in t['source_ports']}),
                  optional_artifact_bindings=len(optional),
                  source_target_participations=sum(len(t['source_ports']) for t in entries),
                  unique_artifacts=len(artifacts),
                  manifest_artifacts=sum(a.get('staging') in {'host', 'runtime'} for a in artifacts.values()),
                  candidate_artifacts=sum(a.get('activation') == 'candidate-only' for a in artifacts.values()),
                  legacy_targets_retired=sum(t['legacy_target_retired'] for t in entries))
    counts['whole_target_replacements_verified'] = sum(t['whole_target_replacement'] for t in entries)
    counts['pending_targets'] = len(entries) - counts['whole_target_replacements_verified']
    counts['unretired_targets'] = len(entries) - counts['legacy_targets_retired']
    targets = dict(schema_version=2, upstream_commit=pin,
                   scope='Captured default graph only. Pending means whole replacement unverified; counts are cardinalities, never effort.',
                   compatibility='v1 names/id/type/graph_reference/status/legacy_target_retired retained; counts corrected. See tools/MIGRATION-LEDGER.txt.',
                   counts=counts, additional_optional_artifacts=optional, targets=entries)
    graph_rows = []
    for path, uses in sorted(participation.items()):
        leaf = leaves.get(path)
        graph_rows.append(dict(path=path, participation=uses,
                               classification=leaf['category'] if leaf else 'unresolved-alias-or-external-input',
                               pin=leaf['commit'] if leaf else None,
                               object_id=leaf['object_id'] if leaf else None))
    unobserved = [p for p, e in leaves.items() if e['category'] == 'source' and p not in participation]
    input_index = []
    for kind in ('manifests_sha256', 'implementation_sha256'):
        for path, expected in ledger['input_snapshot'].get(kind, {}).items():
            file = root / path
            actual = digest(file) if file.is_file() else None
            input_index.append(dict(path=path, recorded_sha256=expected, observed_sha256=actual,
                                    status='matched' if actual == expected else 'changed-or-missing'))
    reconciliation = dict(schema_version=2, upstream_commit=pin, counts=counts,
                          inputs=ledger['input_snapshot'], input_index=input_index, evidence_index=evidence_index,
                          compatibility_v1=ledger.get('compatibility_v1', {}),
                          cargo_packages=packages, artifacts=list(artifacts.values()),
                          candidate_work=ledger.get('candidate_work', []),
                          execution_observations=ledger.get('execution_observations', []),
                          gaps=ledger['gaps'], packages=load(root / ledger['package_scope_path']) if ledger.get('package_scope_path') else ledger.get('packages', []),
                          graph_reconciliation=dict(source_records=source_records,
                              non_generated_unique_paths=len(literal_paths), canonical_non_generated_paths=len(participation),
                              symlink_resolved_records=sum(bool(u['symlink_trace']) and u['resolution'] == 'resolved' for uses in participation.values() for u in uses),
                              unresolved_non_generated_paths=sum(p not in leaves for p in participation),
                              generated_records=sum(map(len, generated.values())),
                              generated_unique_paths=len(generated), shared_source_paths=sum(len({u['target'] for u in uses}) > 1 for uses in participation.values()),
                              upstream_leaf_categories=dict(sorted(Counter(e['category'] for e in leaves.values()).items())),
                              implementation_leaves_absent_from_captured_graph=sorted(unobserved),
                              source_participation=graph_rows,
                              generated_inputs=[dict(path=p, participation=uses) for p, uses in sorted(generated.items())]))
    reconciliation['file_evidence_index'] = []
    for record in ledger.get('file_evidence', []):
        path = root / record['path']
        actual = digest(path) if path.is_file() else None
        reconciliation['file_evidence_index'].append(dict(record, observed_sha256=actual,
            availability='hash-matched' if actual == record['sha256'] else 'missing' if actual is None else 'changed'))
    return targets, reconciliation


def gap_matrix(targets, current):
    lines = ['Dearust current reconciliation: partial implementation and open gates',
             '====================================================================',
             '', 'Generated from exact ledger/graph/manifest inputs; evidence is historical.',
             'Entries, source leaves, artifacts and targets are cardinalities, never effort.',
             'No runtime/framework port or legacy retirement is completed by this ledger.', '',
             'Counts: ' + json.dumps(targets['counts'], sort_keys=True), '',
             'Target | unique source ports | artifact bindings | consumer link | execution | whole replacement | retired',
             '-------|---------------------|-------------------|---------------|-----------|-------------------|--------']
    unmapped = 0
    for target in targets['targets']:
        bindings = target['artifact_bindings']
        if not bindings and not target['source_ports']:
            unmapped += 1
            continue
        lines.append(' | '.join(str(x) for x in (target['name'], len(target['source_ports']), len(bindings),
            any(b['linked'] and b['evidence_currently_available'] for b in bindings),
            any(b['executed'] and b['evidence_currently_available'] for b in bindings),
            target['whole_target_replacement'], target['legacy_target_retired'])))
    lines += ['', f'{unmapped} additional default targets have no mapped Rust work; replacement remains pending.',
              'Source participation records are associations, not execution claims for every consumer.',
              'emulation_dyld has shared source coverage; its static consumer link/execution is unverified.',
              'Host staging/native harness execution does not prove full darlingserver consumer replacement.',
              'Production activation is separate from staging and owned mixed-runtime execution.',
              'Original i386 retained in fat links is original implementation, not Rust coverage.', '',
              'Unresolved / unknown rows', '-------------------------']
    for gap in current['gaps']:
        lines.append(f"{gap['id']} [{gap['status']}]: {gap['missing']}")
    graph = current['graph_reconciliation']
    lines += ['', 'Captured graph reconciliation: ' + json.dumps({k: v for k, v in graph.items()
               if not isinstance(v, (dict, list))}, sort_keys=True),
              f"{len(graph['implementation_leaves_absent_from_captured_graph'])} source-classified leaves absent by literal path from this graph need scope review; they may include tests, generated inputs or optional sources.",
              'Alias/external input rows and full participation edges remain in migration-reconciliation.json.',
              'SDK/header/symlink/data entries retain exact pins and require preservation/review, not automatic translation.',
              '', 'Packages01-09: tools/MIGRATION-PACKAGES.txt and tools/migration-packages.json.',
              'Resource candidate work remains candidate-only; blocked adversarial RPC verification remains unverified.',
              'Next executable work: tools/MIGRATION-LEDGER.txt and tools/MIGRATION-PACKAGES.txt.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--graph', type=Path, default=Path('upstream-cmake-graph.json'))
    parser.add_argument('--ledger', type=Path, default=Path('migration-ledger.json'))
    parser.add_argument('--output', type=Path, default=Path('migration-targets.json'))
    parser.add_argument('--reconciliation', type=Path, default=Path('migration-reconciliation.json'))
    parser.add_argument('--matrix', type=Path, default=Path('migration-gap-matrix.txt'))
    args = parser.parse_args()
    root = args.root
    try:
        targets, current = reconcile(root, load(root / args.graph), load(root / 'migration-inventory.json'),
                                     load(root / 'dependency-inventory.json'), load(root / args.ledger))
        paths = [args.graph, args.ledger, Path('migration-inventory.json'), Path('dependency-inventory.json'),
                 Path('tools/target_inventory.py'), Path('xtask/host-artifacts.tsv'), Path('xtask/runtime-artifacts.tsv')]
        scope_path = load(root / args.ledger).get('package_scope_path')
        if scope_path:
            paths.append(Path(scope_path))
        current['generator_inputs_sha256'] = {str(path): digest(root / path) for path in paths}
        for filename, data in ((args.output, targets), (args.reconciliation, current)):
            (root / filename).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
        (root / args.matrix).write_text(gap_matrix(targets, current))
        print(json.dumps(targets['counts'], sort_keys=True))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'reconciliation failed: {error}\n')


if __name__ == '__main__':
    main()
