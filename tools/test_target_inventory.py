"""Small, hermetic accounting fixtures; no runtime, network or reference builds."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import target_inventory as inventory


PIN = '1' * 40
DEP_PIN = '2' * 40
BLOB = '3' * 40
HOST = 'host:bin/tool'
RUNTIME = 'runtime:objects/port.o'


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'xtask').mkdir()
        (self.root / 'src').mkdir()
        (self.root / 'Cargo.toml').write_text('[package]\nname = "tiny"\nversion = "0.1.0"\n')
        (self.root / 'src/port.rs').write_text('pub fn implemented() -> u32 { 7 }\n')
        (self.root / 'xtask/host-artifacts.tsv').write_text(
            'bin\ttool\ttiny\tbin/tool\tsrc/CMakeLists.txt\n')
        (self.root / 'xtask/runtime-artifacts.tsv').write_text(
            'object\tport\ttiny\t-\tprivate\tobjects/port.o\tsrc/CMakeLists.txt\n')
        evidence = self.root / 'evidence.json'
        evidence.write_text('{"scope":"native fixture execution", "passed":true}\n')
        self.graph = {'objects': {
            'a.json': {'id': 'a-id', 'name': 'a', 'type': 'OBJECT_LIBRARY',
                       'sources': [{'path': '$SOURCE/src/port.c', 'compileGroupIndex': 0}]},
            'b.json': {'id': 'b-id', 'name': 'b', 'type': 'SHARED_LIBRARY', 'sources': []},
        }}
        self.superproject = {'upstream_commit': PIN, 'entries': [
            {'path': 'src/port.c', 'object_id': BLOB, 'category': 'source', 'status': 'partial'},
            {'path': 'sdk/header.h', 'object_id': '4' * 40, 'category': 'header', 'status': 'pending'},
        ]}
        self.dependencies = {'upstream_commit': PIN, 'dependencies': []}
        self.ledger = {
            'schema_version': 2, 'upstream_commit': PIN,
            'cargo_manifests': ['Cargo.toml'], 'input_snapshot': {'fixture': 'exact committed objects'},
            'evidence': [{'id': 'run', 'path': 'evidence.json', 'sha256': inventory.digest(evidence)}],
            'source_ports': [{'source': 'src/port.c', 'object_id': BLOB, 'commit': PIN,
                              'artifacts': [RUNTIME], 'rust_sources': ['src/port.rs'],
                              'status': 'partial', 'open_gates': ['i386']}],
            'bindings': [], 'retirements': [], 'gaps': [{'gate': 'other configurations unknown'}],
        }

    def binding(self, target='a', artifact=RUNTIME, **changes):
        result = {'target': target, 'artifact': artifact, 'scope': 'fixture native ABI',
                  'architectures': ['x86_64'], 'configurations': ['fixture-default'],
                  'compiled': True, 'linked': True, 'executed': True,
                  'activation': 'default-staging', 'production_active': False,
                  'evidence': ['run'], 'open_gates': ['whole target incomplete'],
                  'original_slices_retained': ['i386']}
        result.update(changes)
        return result

    def reconcile(self):
        return inventory.reconcile(self.root, self.graph, self.superproject,
                                   self.dependencies, self.ledger)

    def target(self, targets, name):
        return next(t for t in targets['targets'] if t['name'] == name)

    def test_source_only_target_is_work_but_not_artifact_or_retirement(self):
        targets, report = self.reconcile()
        counts = targets['counts']
        self.assertEqual(counts['with_source_port_participation'], 1)
        self.assertEqual(counts['with_rust_work'], 1)
        self.assertEqual(counts['with_cargo_artifact_slice'], 0)
        self.assertEqual(counts['with_execution_evidence'], 0)
        self.assertEqual(counts['pending_targets'], 2)
        self.assertEqual(self.target(targets, 'a')['status'], 'pending')
        self.assertFalse(self.target(targets, 'a')['whole_target_replacement'])
        self.assertEqual(report['counts'], counts)

    def test_many_artifacts_and_shared_artifact_count_target_and_source_unions(self):
        self.graph['objects']['b.json']['sources'] = [
            {'path': '$SOURCE/src/port.c', 'compileGroupIndex': 1},
            {'path': '$SOURCE/src/port.c', 'compileGroupIndex': 2}]
        self.ledger['bindings'] = [self.binding(), self.binding(artifact=HOST), self.binding(target='b')]
        targets, report = self.reconcile()
        counts = targets['counts']
        self.assertEqual(counts['unique_artifacts'], 2)
        self.assertEqual(counts['with_cargo_artifact_slice'], 2)
        self.assertEqual(counts['unique_ported_upstream_sources'], 1)
        self.assertEqual(counts['source_target_participations'], 2)
        self.assertEqual(counts['with_execution_evidence'], 2)
        self.assertEqual(len(self.target(targets, 'a')['artifact_bindings']), 2)
        self.assertEqual(report['graph_reconciliation']['shared_source_paths'], 1)
        self.assertEqual(len(report['graph_reconciliation']['source_participation'][0]['participation']), 3)

    def test_optional_absent_target_preserved_without_changing_observed_counts(self):
        self.ledger['bindings'] = [self.binding('optional-tool', HOST, optional=True)]
        targets, _ = self.reconcile()
        self.assertEqual(targets['counts']['observed_default_targets'], 2)
        self.assertEqual(targets['counts']['with_cargo_artifact_slice'], 0)
        self.assertEqual(targets['additional_optional_artifacts'][0]['target'], 'optional-tool')
        self.ledger['bindings'][0]['optional'] = False
        with self.assertRaisesRegex(ValueError, 'required target absent'):
            self.reconcile()

    def test_original_i386_retention_is_explicit_and_not_a_port(self):
        self.ledger['bindings'] = [self.binding()]
        targets, _ = self.reconcile()
        target = self.target(targets, 'a')
        self.assertEqual(target['original_architecture_slices_retained'], ['i386'])
        self.assertEqual(target['artifact_bindings'][0]['architectures'], ['x86_64'])
        self.assertFalse(target['legacy_target_retired'])

    def test_candidate_execution_is_distinct_from_production_activation(self):
        self.ledger['bindings'] = [self.binding(activation='candidate-only')]
        self.ledger['candidate_work'] = [{'id': 'owner', 'activation': 'candidate-only',
                                          'open_gates': ['fork ownership']}]
        targets, report = self.reconcile()
        self.assertEqual(targets['counts']['with_execution_evidence'], 1)
        self.assertEqual(targets['counts']['production_active_targets'], 0)
        self.assertEqual(report['candidate_work'], self.ledger['candidate_work'])
        self.ledger['bindings'][0]['production_active'] = True
        with self.assertRaisesRegex(ValueError, 'candidate cannot'):
            self.reconcile()

    def test_stale_and_missing_evidence_exclude_verification_counts_but_preserve_work(self):
        self.ledger['bindings'] = [self.binding(production_active=True)]
        path = self.root / 'evidence.json'
        for state in ['stale', 'missing']:
            with self.subTest(state=state):
                if state == 'stale':
                    path.write_text('{"different run":true}\n')
                else:
                    path.unlink()
                targets, report = self.reconcile()
                self.assertEqual(report['evidence_index'][0]['availability'], state)
                self.assertEqual(targets['counts']['with_rust_work'], 1)
                self.assertEqual(targets['counts']['with_cargo_artifact_slice'], 1)
                for key in ['with_consumer_link_evidence', 'with_execution_evidence', 'production_active_targets']:
                    self.assertEqual(targets['counts'][key], 0)
                self.assertTrue(self.target(targets, 'a')['artifact_bindings'][0]['executed'])

    def test_contradictory_or_unproven_binding_claims_rejected(self):
        cases = [{'linked': False}, {'compiled': False}, {'evidence': []},
                 {'evidence': ['unknown']}, {'architectures': []}, {'configurations': []}, {'scope': ''}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.ledger['bindings'] = [self.binding(**changes)]
                self.reconcile()

    def test_candidate_work_record_alone_does_not_inflate_artifact_or_source_counts(self):
        before, _ = self.reconcile()
        self.ledger['candidate_work'] = [{'id': 'unlinked-owner', 'activation': 'candidate-only',
                                          'source': 'work/owned.rs', 'compiled': True}]
        after, report = self.reconcile()
        self.assertEqual(before['counts'], after['counts'])
        self.assertEqual(len(report['candidate_work']), 1)

    def test_missing_implementation_or_artifact_is_rejected(self):
        original = copy.deepcopy(self.ledger)
        cases = ['artifact', 'rust_missing', 'rust_not_rust']
        for change in cases:
            with self.subTest(change=change):
                self.ledger = copy.deepcopy(original)
                port = self.ledger['source_ports'][0]
                if change == 'artifact':
                    port['artifacts'] = ['runtime:unknown.o']
                elif change == 'rust_missing':
                    port['rust_sources'] = ['src/missing.rs']
                else:
                    port['rust_sources'] = ['Cargo.toml']
                with self.assertRaises(ValueError):
                    self.reconcile()

    def test_stale_evidence_cannot_retire_a_target(self):
        self.ledger['retirements'] = [self.retirement()]
        self.ledger['bindings'] = [self.binding(production_active=True, open_gates=[], original_slices_retained=[])]
        (self.root / 'evidence.json').write_text('changed historical evidence')
        with self.assertRaisesRegex(ValueError, 'retirement requires'):
            self.reconcile()

    def test_empty_source_implementation_or_artifact_cannot_count_as_rust_work(self):
        for field in ['rust_sources', 'artifacts']:
            with self.subTest(field=field):
                original = self.ledger['source_ports'][0][field]
                self.ledger['source_ports'][0][field] = []
                try:
                    with self.assertRaisesRegex(ValueError, 'requires implementation and artifact'):
                        self.reconcile()
                finally:
                    self.ledger['source_ports'][0][field] = original

    def test_test_fixture_and_benchmark_rust_files_are_not_replacements(self):
        for directory in ['tests', 'fixtures', 'benches']:
            with self.subTest(directory=directory):
                path = self.root / directory / 'proof.rs'
                path.parent.mkdir()
                path.write_text('fn fixture_only() {}')
                self.ledger['source_ports'][0]['rust_sources'] = [str(path.relative_to(self.root))]
                with self.assertRaisesRegex(ValueError, 'missing Rust implementation'):
                    self.reconcile()

    def test_source_legacy_status_cannot_drift_from_exact_inventory(self):
        self.ledger['source_ports'][0]['legacy_status'] = 'implemented'
        with self.assertRaisesRegex(ValueError, 'status disagrees'):
            self.reconcile()
        self.ledger['source_ports'][0]['legacy_status'] = 'partial'
        targets, _ = self.reconcile()
        self.assertEqual(targets['counts']['unique_sources_with_rust_work'], 1)

    def test_invalid_manifest_kind_and_runtime_features_rejected(self):
        path = self.root / 'xtask/runtime-artifacts.tsv'
        valid = path.read_text()
        for content in [valid.replace('object\t', 'unknown\t', 1),
                        valid.replace('\t-\t', '\tmissing-feature\t', 1)]:
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    self.reconcile()
        (self.root / 'Cargo.toml').write_text(
            '[package]\nname = "tiny"\nversion = "0.1.0"\n[features]\nported = []\n')
        path.write_text(valid.replace('\t-\t', '\tported\t', 1))
        targets, _ = self.reconcile()
        self.assertEqual(targets['counts']['unique_artifacts'], 2)

    def test_verification_claims_require_actual_booleans(self):
        for field in ['compiled', 'linked', 'executed', 'production_active']:
            for value in ['false', 1, None]:
                with self.subTest(field=field, value=value):
                    self.ledger['bindings'] = [self.binding(**{field: value})]
                    with self.assertRaisesRegex(ValueError, 'flags must be booleans'):
                        self.reconcile()

    def test_production_activation_requires_compilation_link_and_execution(self):
        cases = [{'compiled': False, 'linked': False, 'executed': False},
                 {'linked': False, 'executed': False}, {'executed': False}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.ledger['bindings'] = [self.binding(production_active=True, **changes)]
                with self.assertRaisesRegex(ValueError, 'production activation requires'):
                    self.reconcile()

    def test_retirement_rejects_pending_or_unknown_compiled_source(self):
        self.ledger['source_ports'][0]['open_gates'] = []
        self.ledger['bindings'] = [self.binding(production_active=True, open_gates=[], original_slices_retained=[])]
        self.ledger['retirements'] = [self.retirement()]
        self.graph['objects']['a.json']['sources'].append(
            {'path': '$SOURCE/src/unported.c', 'compileGroupIndex': 0})
        with self.assertRaisesRegex(ValueError, 'retirement requires'):
            self.reconcile()
        self.superproject['entries'].append({'path': 'src/unported.c', 'object_id': '8' * 40,
                                             'category': 'source', 'status': 'pending'})
        with self.assertRaisesRegex(ValueError, 'retirement requires'):
            self.reconcile()

    def test_source_semantic_gate_blocks_target_retirement(self):
        self.ledger['bindings'] = [self.binding(production_active=True, open_gates=[], original_slices_retained=[])]
        self.ledger['retirements'] = [self.retirement()]
        with self.assertRaisesRegex(ValueError, 'retirement requires'):
            self.reconcile()

    def test_unknown_retirement_target_is_not_silently_ignored(self):
        self.ledger['retirements'] = [dict(self.retirement(), target='unknown')]
        with self.assertRaisesRegex(ValueError, 'retirement target absent'):
            self.reconcile()

    def test_duplicate_records_rejected(self):
        cases = ['upstream', 'dependency', 'target', 'artifact', 'source', 'binding', 'evidence', 'package', 'retirement']
        original = copy.deepcopy((self.graph, self.superproject, self.dependencies, self.ledger))
        for kind in cases:
            with self.subTest(kind=kind):
                self.graph, self.superproject, self.dependencies, self.ledger = copy.deepcopy(original)
                if kind == 'upstream':
                    self.superproject['entries'].append(copy.deepcopy(self.superproject['entries'][0]))
                elif kind == 'dependency':
                    self.dependencies['dependencies'] = [{'path': 'dep', 'commit': DEP_PIN, 'entries': []}] * 2
                elif kind == 'target':
                    self.graph['objects']['other.json'] = copy.deepcopy(self.graph['objects']['a.json'])
                elif kind == 'artifact':
                    self.ledger['extra_artifacts'] = inventory.manifests(self.root)[:1]
                elif kind == 'source':
                    self.ledger['source_ports'] *= 2
                elif kind == 'binding':
                    self.ledger['bindings'] = [self.binding(), self.binding()]
                elif kind == 'evidence':
                    self.ledger['evidence'] *= 2
                elif kind == 'package':
                    self.ledger['cargo_manifests'] *= 2
                else:
                    self.ledger['retirements'] = [{'target': 'a', 'retired': False}] * 2
                with self.assertRaisesRegex(ValueError, 'duplicate'):
                    self.reconcile()

    def test_duplicate_json_keys_rejected_including_nested_records(self):
        for raw in ['{"schema_version":2,"schema_version":1}', '{"evidence":{"id":1,"id":2}}']:
            with self.subTest(raw=raw):
                path = self.root / 'duplicate.json'
                path.write_text(raw)
                with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
                    inventory.load(path)

    def test_duplicate_manifest_destination_rejected(self):
        path = self.root / 'xtask/runtime-artifacts.tsv'
        path.write_text(path.read_text() * 2)
        with self.assertRaisesRegex(ValueError, 'duplicate manifest artifact'):
            inventory.manifests(self.root)

    def test_missing_manifests_fail_without_silent_empty_accounting(self):
        for filename in ['Cargo.toml', 'xtask/host-artifacts.tsv', 'xtask/runtime-artifacts.tsv']:
            with self.subTest(filename=filename):
                path = self.root / filename
                content = path.read_text()
                path.unlink()
                try:
                    with self.assertRaises(FileNotFoundError):
                        self.reconcile()
                finally:
                    path.write_text(content)
        self.ledger['cargo_manifests'] = []
        with self.assertRaisesRegex(ValueError, 'missing package manifest'):
            self.reconcile()

    def retirement(self):
        return {'target': 'a', 'retired': True, 'all_consumers_verified': True,
                'all_configurations_verified': True, 'all_architectures_verified': True,
                'semantics_passed': True, 'performance_passed': True, 'legacy_build_removed': True}

    def test_retirement_requires_every_completion_gate_and_verified_active_binding(self):
        self.ledger['bindings'] = [self.binding(production_active=True, open_gates=[], original_slices_retained=[])]
        self.ledger['source_ports'][0].update(status='implemented', open_gates=[])
        self.superproject['entries'][0]['status'] = 'implemented'
        complete = self.retirement()
        self.ledger['retirements'] = [complete]
        targets, _ = self.reconcile()
        self.assertTrue(self.target(targets, 'a')['whole_target_replacement'])
        self.assertEqual(targets['counts']['legacy_targets_retired'], 1)
        self.assertEqual(targets['counts']['pending_targets'], 1)
        for gate in set(complete) - {'target', 'retired'}:
            with self.subTest(gate=gate):
                self.ledger['retirements'] = [dict(complete, **{gate: False})]
                with self.assertRaisesRegex(ValueError, 'retirement requires'):
                    self.reconcile()
        self.ledger['retirements'] = [complete]
        for changes in [{'open_gates': ['semantics']}, {'production_active': False},
                        {'executed': False}, {'original_slices_retained': ['i386']}]:
            with self.subTest(changes=changes):
                self.ledger['bindings'] = [self.binding(production_active=True, open_gates=[], original_slices_retained=[])]
                self.ledger['bindings'][0].update(changes)
                with self.assertRaises(ValueError):
                    self.reconcile()

    def test_source_pins_blobs_categories_and_annotations_are_exact(self):
        originals = copy.deepcopy((self.superproject, self.dependencies, self.ledger))
        for change in ['super_pin', 'dependency_pin', 'source_pin', 'source_blob', 'header', 'annotation']:
            with self.subTest(change=change):
                self.superproject, self.dependencies, self.ledger = copy.deepcopy(originals)
                if change == 'super_pin':
                    self.superproject['upstream_commit'] = DEP_PIN
                elif change == 'dependency_pin':
                    self.dependencies['upstream_commit'] = DEP_PIN
                elif change == 'source_pin':
                    self.ledger['source_ports'][0]['commit'] = DEP_PIN
                elif change == 'source_blob':
                    self.ledger['source_ports'][0]['object_id'] = '9' * 40
                elif change == 'header':
                    self.superproject['entries'][0]['category'] = 'header'
                else:
                    self.superproject['entries'][0]['status'] = 'pending'
                with self.assertRaises(ValueError):
                    self.reconcile()

    def test_dependency_port_uses_nested_commit_not_superproject_pin(self):
        source = self.superproject['entries'].pop(0)
        self.dependencies['dependencies'] = [{'path': 'dep', 'commit': DEP_PIN, 'entries': [source]}]
        self.ledger['source_ports'][0].update(source='dep/src/port.c', commit=DEP_PIN)
        self.graph['objects']['a.json']['sources'][0]['path'] = '$SOURCE/dep/src/port.c'
        targets, _ = self.reconcile()
        self.assertEqual(targets['counts']['unique_ported_upstream_sources'], 1)
        self.ledger['source_ports'][0]['commit'] = PIN
        with self.assertRaisesRegex(ValueError, 'source pin/blob mismatch'):
            self.reconcile()

    def test_generated_inputs_are_not_rust_replacements_and_missing_sources_remain_visible(self):
        self.graph['objects']['a.json']['sources'].extend([
            {'path': '$BUILD/generated.c', 'isGenerated': True, 'compileGroupIndex': 0},
            {'path': '$SOURCE/unknown/alias.c', 'compileGroupIndex': 0}])
        self.superproject['entries'].append({'path': 'optional/never.c', 'object_id': '5' * 40,
                                             'category': 'source', 'status': 'pending'})
        targets, report = self.reconcile()
        self.assertEqual(targets['counts']['unique_ported_upstream_sources'], 1)
        graph = report['graph_reconciliation']
        self.assertEqual(graph['source_records'], 3)
        self.assertEqual(graph['generated_records'], 1)
        self.assertEqual(graph['implementation_leaves_absent_from_captured_graph'], ['optional/never.c'])
        unresolved = next(r for r in graph['source_participation'] if r['path'] == 'unknown/alias.c')
        self.assertEqual(unresolved['classification'], 'unresolved-alias-or-external-input')

    def test_deterministic_reconciliation_ignores_clock_reference_files_and_root_location(self):
        self.ledger['bindings'] = [self.binding()]
        before = json.dumps(self.reconcile(), sort_keys=True)
        (self.root / 'mutable-reference.c').write_text('uncommitted unrelated reference changes')
        with patch('time.time', side_effect=AssertionError('clock consulted')):
            after = json.dumps(self.reconcile(), sort_keys=True)
        self.assertEqual(before, after)
        with tempfile.TemporaryDirectory() as other:
            import shutil
            moved = Path(other) / 'copy'
            shutil.copytree(self.root, moved)
            result = inventory.reconcile(moved, self.graph, self.superproject, self.dependencies, self.ledger)
            self.assertEqual(before, json.dumps(result, sort_keys=True))
        self.assertNotIn(str(self.root), before)
        self.assertNotIn('timestamp', before)


class V1MigrationTests(unittest.TestCase):
    def fixture(self):
        return {'schema_version': 1, 'upstream_commit': PIN,
                'scope': 'Historical captured default graph',
                'counts': {'observed_default_targets': 4, 'with_cargo_artifact_slice': 1,
                           'pending_targets': 3, 'legacy_targets_retired': 0},
                'additional_optional_artifacts': [{'name': 'optional-tool',
                    'cargo_package': 'tiny', 'status': 'host-artifact-verified',
                    'absent_from_default_configuration': True}],
                'targets': [
                    {'id': 'host-id', 'name': 'host', 'status': 'host-artifact-verified',
                     'cargo_package': 'tiny', 'cargo_artifact': 'bin/tool',
                     'legacy_target_retired': False},
                    {'id': 'slice-id', 'name': 'slice', 'status': 'pending',
                     'verified_source_slices': [{'source': 'errno.c', 'scope': 'native only'}]},
                    {'id': 'consumer-id', 'name': 'consumer', 'status': 'pending',
                     'legacy_consumer_integration': {'scope': 'mixed final link, i386 retained'}},
                    {'id': 'plain-id', 'name': 'plain', 'status': 'pending'}]}

    def test_v1_annotations_and_optional_artifacts_preserved_without_invented_stages(self):
        original = self.fixture()
        preserved = copy.deepcopy(original)
        result = inventory.import_v1(original)
        self.assertEqual(original, preserved)
        self.assertEqual(result['upstream_commit'], PIN)
        self.assertEqual(result['counts'], original['counts'])
        self.assertEqual(result['source_scope'], original['scope'])
        self.assertEqual(result['target_annotations'], original['targets'][:3])
        self.assertEqual(result['additional_optional_artifacts'], original['additional_optional_artifacts'])
        for annotation in result['target_annotations']:
            for stage in ['compiled', 'linked', 'executed', 'production_active', 'whole_target_replacement']:
                self.assertNotIn(stage, annotation)
        self.assertIn('Historical', result['scope'])

    def test_v1_import_rejects_wrong_schema_duplicate_ids_and_count_mismatch(self):
        for change in ['schema', 'duplicate', 'count']:
            with self.subTest(change=change):
                original = self.fixture()
                if change == 'schema':
                    original['schema_version'] = 2
                elif change == 'duplicate':
                    original['targets'][1]['id'] = original['targets'][0]['id']
                else:
                    original['counts']['observed_default_targets'] = 5
                with self.assertRaises(ValueError):
                    inventory.import_v1(original)

    def test_v1_import_cli_preserves_input_bytes_and_records_deterministic_digest(self):
        import subprocess
        import sys
        script = Path(inventory.__file__).with_name('import_target_v1.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, first, second = (root / name for name in ['legacy.json', 'first.json', 'second.json'])
            source.write_text(json.dumps(self.fixture(), indent=2) + '\n')
            before = source.read_bytes()
            for destination in [first, second]:
                subprocess.run([sys.executable, str(script), str(source), str(destination)],
                               check=True, capture_output=True, text=True)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            result = inventory.load(first)
            self.assertEqual(result['input_sha256'], inventory.digest(source))
            self.assertEqual(result['target_annotations'], self.fixture()['targets'][:3])


if __name__ == '__main__':
    unittest.main()
