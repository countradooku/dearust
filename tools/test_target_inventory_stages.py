"""Evidence contradiction and independent replacement/retirement contracts."""
import unittest
import test_target_inventory as fixtures
from target_inventory import digest


class EvidenceStageTests(unittest.TestCase):
    setUp = fixtures.ReconciliationTests.setUp
    binding = fixtures.ReconciliationTests.binding
    reconcile = fixtures.ReconciliationTests.reconcile
    target = fixtures.ReconciliationTests.target
    retirement = fixtures.ReconciliationTests.retirement

    def test_hash_valid_report_with_contradictory_selected_fact_cannot_verify(self):
        path = self.root / 'evidence.json'
        path.write_text('{"native":{"passed":false}}\n')
        self.ledger['evidence'][0].update(sha256=digest(path), assertions=[
            {'pointer': '/native/passed', 'equals': True}])
        self.ledger['bindings'] = [self.binding()]
        targets, report = self.reconcile()
        self.assertEqual(report['evidence_index'][0]['availability'], 'contradictory')
        self.assertEqual(targets['counts']['with_execution_evidence'], 0)
        self.assertEqual(targets['counts']['with_rust_work'], 1)
        self.ledger['evidence'][0]['assertions'][0]['equals'] = False
        targets, report = self.reconcile()
        self.assertEqual(report['evidence_index'][0]['availability'], 'hash-matched')
        # Assertion interpretation is explicitly scoped by the ledger; it is
        # not a heuristic that every false flag in a partial report is failure.
        self.assertEqual(targets['counts']['with_execution_evidence'], 1)

    def test_missing_asserted_fact_and_wrong_type_are_contradictory(self):
        self.ledger['bindings'] = [self.binding()]
        for pointer, expected in [('/missing', True), ('/passed', 1)]:
            with self.subTest(pointer=pointer):
                self.ledger['evidence'][0]['assertions'] = [{'pointer': pointer, 'equals': expected}]
                targets, report = self.reconcile()
                self.assertEqual(report['evidence_index'][0]['availability'], 'contradictory')
                self.assertEqual(targets['counts']['with_execution_evidence'], 0)

    def test_complete_replacement_does_not_imply_legacy_retirement(self):
        self.ledger['bindings'] = [self.binding(production_active=True, open_gates=[], original_slices_retained=[])]
        self.ledger['source_ports'][0].update(status='implemented', open_gates=[])
        self.superproject['entries'][0]['status'] = 'implemented'
        completion = self.retirement()
        completion.pop('retired')
        completion.pop('legacy_build_removed')
        completion['verified'] = True
        self.ledger['whole_target_replacements'] = [completion]
        targets, _ = self.reconcile()
        target = self.target(targets, 'a')
        self.assertTrue(target['whole_target_replacement'])
        self.assertFalse(target['legacy_target_retired'])
        self.assertEqual(target['status'], 'replacement-verified')
        self.assertEqual(targets['counts']['whole_target_replacements_verified'], 1)
        self.assertEqual(targets['counts']['pending_targets'], 1)
        self.assertEqual(targets['counts']['unretired_targets'], 2)
