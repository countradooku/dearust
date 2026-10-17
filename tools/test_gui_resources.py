import hashlib
from pathlib import Path
import plistlib
import tempfile
import unittest

from build_gui_apps import repair_legacy_keyed_archive


class LegacyNibTests(unittest.TestCase):
    def test_repairs_binary_dictionary_references_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'keyedobjects.nib'
            archive = {'$archiver': 'NSKeyedArchiver', '$objects': ['$null', {'name': 'view'}],
                       '$top': {'root': {'CF$UID': 1}}}
            path.write_bytes(plistlib.dumps(archive, fmt=plistlib.FMT_BINARY))
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = repair_legacy_keyed_archive(path)
            self.assertEqual(result['converted_references'], 1)
            self.assertEqual(result['original_sha256'], before)
            self.assertEqual(plistlib.loads(path.read_bytes())['$top']['root'], plistlib.UID(1))
            self.assertEqual(repair_legacy_keyed_archive(path)['converted_references'], 0)

    def test_invalid_reference_does_not_modify_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'keyedobjects.nib'
            data = plistlib.dumps({'$archiver': 'NSKeyedArchiver', '$objects': ['$null'],
                                  '$top': {'root': {'CF$UID': 9}}}, fmt=plistlib.FMT_BINARY)
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                repair_legacy_keyed_archive(path)
            self.assertEqual(path.read_bytes(), data)
