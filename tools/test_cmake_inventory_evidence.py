"""Pinned declaration evidence cannot add configured targets or hide unknowns."""
import copy
import json
from pathlib import Path
import unittest

import test_target_inventory as fixtures
from cmake_declaration_inventory import parse, sources
from target_inventory import digest


class DeclarationEvidenceTests(unittest.TestCase):
    setUp = fixtures.ReconciliationTests.setUp
    reconcile = fixtures.ReconciliationTests.reconcile

    def prepare(self, malformed=False):
        (self.root / 'tools').mkdir()
        tool = Path(__file__).with_name('cmake_declaration_inventory.py')
        (self.root / 'tools' / tool.name).write_bytes(tool.read_bytes())
        self.superproject['entries'].append(dict(path='CMakeLists.txt', mode='100644',
            object_type='blob', object_id='5' * 40, category='build-cmake', status='pending'))
        for name, data in [('migration-inventory.json', self.superproject),
                           ('dependency-inventory.json', self.dependencies)]:
            (self.root / name).write_text(json.dumps(data))
        row = sources(self.superproject, self.dependencies)[0]
        commands = parse('if(OPT)\nadd_library(optional STATIC x.c)\nendif()\n')
        row.update(declarations=commands, parse_status='valid', sha256='a' * 64, size=52)
        categories, names = {}, {}
        for command in commands:
            categories[command['category']] = categories.get(command['category'], 0) + 1
            names[command['command']] = names.get(command['command'], 0) + 1
        self.report = dict(schema_version=1, upstream_commit=fixtures.PIN,
            inputs=dict(superproject_inventory_sha256=digest(self.root/'migration-inventory.json'),
                dependency_inventory_sha256=digest(self.root/'dependency-inventory.json'),
                tool_sha256=digest(self.root/'tools'/tool.name),
                repositories=[dict(path='', commit=fixtures.PIN)]),
            valid=True, coverage=dict(selected_exact_inputs_verified=True,
                all_commands_parsed=True, handwritten_semantic_review_complete=False),
            counts=dict(files=1, repositories=1, declarations=3, parse_invalid_files=0,
                by_command=names, by_category=categories),
            files=[row], parse_errors=[], unresolved=['Conditions are not evaluated.'])
        if malformed:
            row.update(declarations=[], parse_status='invalid-or-non-script', parse_error='fixture invalid')
            self.report.update(valid=False, parse_errors=[dict(path=row['path'], error='fixture invalid')])
            self.report['coverage']['all_commands_parsed'] = False
            self.report['counts'].update(declarations=0, parse_invalid_files=1,
                                          by_command={}, by_category={})
        self.write_report()

    def write_report(self):
        path = self.root/'declarations.json'
        path.write_text(json.dumps(self.report))
        self.ledger['build_declaration_inventory'] = dict(path=path.name, sha256=digest(path))

    def test_conditional_absent_declaration_does_not_add_a_configured_target(self):
        self.prepare()
        targets, report = self.reconcile()
        self.assertEqual(targets['counts']['observed_default_targets'], 2)
        row = report['graph_reconciliation']['cmake_declarations']
        self.assertEqual([r['name'] for r in row['literal_targets_absent_from_captured_graph']], ['optional'])
        self.assertFalse(row['configuration_evaluated'])
        self.assertFalse(row['commands_executed'])
        self.assertFalse(row['complete_configured_closure'])
        self.assertEqual(self.reconcile(), (targets, report))

    def test_unparsed_exact_input_remains_explicit_and_is_not_execution(self):
        self.prepare(malformed=True)
        targets, report = self.reconcile()
        row = report['graph_reconciliation']['cmake_declarations']
        self.assertTrue(row['provenance_verified'])
        self.assertEqual(row['unparsed_files'], ['CMakeLists.txt'])
        self.assertFalse(row['coverage']['all_commands_parsed'])
        self.assertEqual(targets['counts']['with_execution_evidence'], 0)

    def test_hash_current_but_contradictory_provenance_counts_or_claims_fail(self):
        self.prepare()
        original = copy.deepcopy(self.report)
        for mutate in (
            lambda r: r['files'][0].update(commit='9'*40),
            lambda r: r['files'].append(copy.deepcopy(r['files'][0])),
            lambda r: r['files'].clear(),
            lambda r: r['counts'].update(declarations=99),
            lambda r: r['counts'].update(files=True),
            lambda r: r['coverage'].update(all_commands_parsed=1),
            lambda r: r['coverage'].update(handwritten_semantic_review_complete=True),
            lambda r: r['inputs']['repositories'].clear(),
        ):
            with self.subTest(mutation=mutate):
                self.report = copy.deepcopy(original)
                mutate(self.report)
                self.write_report()
                with self.assertRaises(ValueError):
                    self.reconcile()

    def test_missing_stale_and_drifted_inputs_fail_closed(self):
        self.prepare()
        path = self.root/'declarations.json'
        original = path.read_bytes()
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.reconcile()
        path.unlink()
        with self.assertRaises(OSError):
            self.reconcile()
        path.write_bytes(original)
        (self.root/'tools/cmake_declaration_inventory.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'input drift'):
            self.reconcile()

    def test_pinned_role_classification_cannot_turn_invalid_text_into_execution(self):
        self.prepare(malformed=True)
        source = self.report['files'][0]
        review = dict(schema_version=1, upstream_commit=fixtures.PIN,
            inputs=dict(declaration_inventory_sha256=self.ledger['build_declaration_inventory']['sha256']),
            files=[dict(path=source['path'], commit=source['commit'], object_id=source['object_id'],
                sha256=source['sha256'], role='prose', review_gate='Reachability is unknown.',
                consumer_declarations=[], declaration_or_role_presence_establishes_execution=False)],
            counts_by_role=dict(prose=1), reviewed_files=1,
            completion=dict(configured_or_executed=False, reachability_resolved=False,
                            handwritten_CMake_semantic_review_complete=False),
            remaining_gates=['No configuration executed.'])
        path = self.root/'roles.json'
        def write():
            path.write_text(json.dumps(review))
            self.ledger['build_declaration_inventory']['role_review'] = dict(path=path.name, sha256=digest(path))
        write()
        _, report = self.reconcile()
        result = report['graph_reconciliation']['cmake_declarations']
        self.assertFalse(result['coverage']['all_commands_parsed'])
        self.assertTrue(result['input_role_review']['all_unparsed_inputs_role_classified'])
        self.assertFalse(result['input_role_review']['commands_executed'])
        for key, value in [('object_id', '9'*40), ('declaration_or_role_presence_establishes_execution', True)]:
            old = review['files'][0][key]
            review['files'][0][key] = value
            write()
            with self.assertRaises(ValueError):
                self.reconcile()
            review['files'][0][key] = old


if __name__ == '__main__':
    unittest.main()
