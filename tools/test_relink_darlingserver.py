import tempfile
from pathlib import Path
import unittest

from relink_darlingserver import replacements, validate_output


class ServerRelinkTests(unittest.TestCase):
    def test_output_cannot_enter_original_source_or_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            build = root / 'build'
            source.mkdir()
            build.mkdir()
            (build / 'CMakeCache.txt').write_text('CMAKE_HOME_DIRECTORY:INTERNAL=' + str(source))
            for output in (source, source / 'new', build, build / 'new'):
                with self.assertRaises(ValueError):
                    validate_output(build, output)
            validate_output(build, root / 'isolated')

    def test_invalid_replacement_paths_are_rejected(self):
        for value in ('/absolute.c=missing', '../escape.c=missing', 'not-a-replacement'):
            with self.assertRaises(ValueError):
                replacements([value])

    def test_duplicate_replacement_is_rejected(self):
        with tempfile.NamedTemporaryFile() as object_file:
            value = 'src/task.c=' + object_file.name
            with self.assertRaises(ValueError):
                replacements([value, value])


if __name__ == '__main__':
    unittest.main()
