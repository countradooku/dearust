"""Committed-path resolution: aliases retain provenance without host symlink access."""
import unittest
from target_inventory import resolve_source


class InventoryResolutionTests(unittest.TestCase):
    def test_relative_directory_alias_and_parent_traversal_preserve_blob_provenance(self):
        leaves = {
            'src/alias': {'mode': '120000', 'symlink_target': '../implementation', 'object_id': '1' * 40, 'commit': 'a' * 40},
            'implementation/file.c': {'mode': '100644', 'object_id': '2' * 40},
            'implementation/nested/link': {'mode': '120000', 'symlink_target': '../file.c', 'object_id': '3' * 40},
        }
        path, trace, status = resolve_source('src/alias/nested/link', leaves)
        self.assertEqual(path, 'implementation/file.c')
        self.assertEqual(status, 'resolved')
        self.assertEqual([r['object_id'] for r in trace], ['1' * 40, '3' * 40])
        # '..' follows the expanded symlink path, not its original parent.
        path, _, status = resolve_source('src/alias/nested/../file.c', leaves)
        self.assertEqual((path, status), ('implementation/file.c', 'resolved'))

    def test_absolute_escape_cycle_and_missing_targets_remain_explicit_unknowns(self):
        cases = [('/host/secret', 'absolute-sdk-or-host-symlink'),
                 ('../../outside', 'escapes-inventory-root'),
                 ('loop', 'symlink-cycle-or-depth-unresolved'),
                 (None, 'symlink-target-not-captured')]
        for target, expected in cases:
            with self.subTest(target=target):
                entry = {'mode': '120000', 'object_id': '1' * 40}
                if target is not None:
                    entry['symlink_target'] = target
                _, trace, state = resolve_source('loop', {'loop': entry})
                self.assertEqual(state, expected)
                self.assertTrue(trace)
        self.assertEqual(resolve_source('unknown.c', {})[2], 'unresolved-alias-or-external-input')
        self.assertEqual(resolve_source('$BUILD/source.c', {})[2], 'external-root')

    def test_parent_components_cannot_erase_a_missing_directory_or_regular_file(self):
        leaves = {'file.c': {'mode': '100644', 'object_id': '2' * 40}}
        for path in ['missing/../file.c', 'file.c/../file.c']:
            with self.subTest(path=path):
                self.assertEqual(resolve_source(path, leaves)[2], 'traverses-missing-directory-or-file')
