import importlib.util
from pathlib import Path
import os
import signal
import sys
import tempfile
import unittest
from unittest import mock
import subprocess

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

    def fake_process(self, events):
        process = mock.Mock(pid=12345, returncode=None)
        iterator = iter(events)
        def wait(timeout):
            event = next(iterator)
            if isinstance(event, Exception):
                raise event
            process.returncode = event
            return event
        process.wait.side_effect = wait
        return process

    def test_shutdown_grace_reaps_without_signaling_setuid_launcher(self):
        process = self.fake_process([0])
        with mock.patch.object(apps.os, 'killpg') as kill:
            record = apps.reap_launcher(process, grace=5)
        kill.assert_not_called()
        process.wait.assert_called_once_with(timeout=5)
        self.assertTrue(record['reaped'])

    def test_permission_denied_signal_is_explicit_unreaped_failure(self):
        timeout = subprocess.TimeoutExpired('launcher', 5)
        process = self.fake_process([timeout, timeout])
        with mock.patch.object(apps.os, 'killpg', side_effect=PermissionError('setuid launcher')):
            record = apps.reap_launcher(process)
        self.assertFalse(record['reaped'])
        self.assertIn('setuid launcher', record['signal_error'])
        self.assertIn('bounded cleanup', record['error'])
        self.assertIsNone(record['exit_code'])

    def test_exit_between_grace_and_signal_is_reaped(self):
        process = self.fake_process([subprocess.TimeoutExpired('launcher', 5), 0])
        with mock.patch.object(apps.os, 'killpg', side_effect=ProcessLookupError()):
            record = apps.reap_launcher(process)
        self.assertTrue(record['reaped'])
        self.assertIn('signal_race', record)

    def test_timeout_shuts_down_only_explicit_callback_prefix(self):
        process = self.fake_process([subprocess.TimeoutExpired('launcher', 1), 0])
        callback = mock.Mock(return_value={'exit_code': 0})
        with mock.patch.object(apps.subprocess, 'Popen', return_value=process), mock.patch.object(apps.os, 'killpg') as kill:
            record = apps.run_process(['launcher'], {}, 1, on_timeout=callback)
        callback.assert_called_once_with()
        kill.assert_not_called()
        self.assertTrue(record['timed_out'])
        self.assertTrue(record['cleanup']['reaped'])
        self.assertEqual(record['timeout_shutdown'], {'exit_code': 0})

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
