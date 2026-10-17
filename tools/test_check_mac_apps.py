import importlib.util
from pathlib import Path
import os
import signal
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('check_mac_apps', Path(__file__).with_name('check_mac_apps.py'))
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)


class CaptureTests(unittest.TestCase):
    def test_daemon_inheriting_capture_descriptors_does_not_block(self):
        # This child deliberately keeps stdout open after the launcher exits.
        code = "import os,time; pid=os.fork(); print(pid,flush=True) if pid else time.sleep(30)"
        record = apps.run_process([sys.executable, '-c', code], os.environ.copy(), 1)
        child = int(record['stdout'].strip())
        try:
            self.assertEqual(record['exit_code'], 0)
            self.assertFalse(record['timed_out'])
            self.assertLess(record['seconds'], 2)
        finally:
            os.kill(child, signal.SIGKILL)

    def test_timeout_kills_and_reaps_exact_launcher_group(self):
        record = apps.run_process([sys.executable, '-c', 'import time; time.sleep(30)'], os.environ.copy(), 0.05)
        self.assertTrue(record['timed_out'])
        self.assertLess(record['exit_code'], 0)
        self.assertLess(record['seconds'], 2)

    def test_timeout_cannot_pass_from_stale_child_completion(self):
        result = apps.classify({'exit_code': 0, 'timed_out': True}, 'ok\n', 0, 'ok\n', 'test')
        self.assertFalse(result['strict_pass'])
        self.assertFalse(result['normalized_pass'])

    def test_missing_child_status_is_failure(self):
        self.assertFalse(apps.classify({'exit_code': 0, 'timed_out': False}, '', None, '', 'test')['strict_pass'])

    def test_normalization_remains_separate_from_strict_result(self):
        text = 'Test\n2026-10-18 01:54:05.040 cfbridge.m.bin[281:3e8] Test\n4294967295\n2\n'
        result = apps.classify({'exit_code': 0, 'timed_out': False}, text, 0, 'Test\n4294967295\n2\n', 'cfbridge.m')
        self.assertFalse(result['strict_pass'])
        self.assertTrue(result['normalized_pass'])
        self.assertFalse(apps.classify({'exit_code': 0, 'timed_out': False}, text, 0, 'Test\n4294967295\n2\n', 'other')['normalized_pass'])

    def test_capture_status_is_shell_variable_and_old_files_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for suffix in ('stdout', 'stderr', 'exit'):
                (output / ('test.' + suffix)).write_text('stale')
            command, *paths = apps.shell_capture(['/bin/echo', "a'b"], output, 'test')
            self.assertIn('"$result"', command)
            self.assertNotIn('\\"$result', command)
            self.assertIn("printf '%s\\n'", command)
            self.assertTrue(all(not path.exists() for path in paths))


if __name__ == '__main__':
    unittest.main()
