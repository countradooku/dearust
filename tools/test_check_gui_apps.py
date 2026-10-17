import subprocess
import unittest
from unittest import mock

import check_gui_apps as gui


class LauncherCleanupTests(unittest.TestCase):
    def test_namespace_shutdown_allows_control_cli_to_exit(self):
        process = mock.Mock(pid=12345, returncode=0)
        with mock.patch.object(gui.os, 'killpg') as kill:
            result = gui.terminate_launcher(process)
        process.wait.assert_called_once_with(timeout=5)
        kill.assert_not_called()
        self.assertTrue(result['reaped'])

    def test_setuid_permission_denied_preserves_explicit_failure(self):
        process = mock.Mock(pid=12345, returncode=None)
        process.wait.side_effect = subprocess.TimeoutExpired('setuid launcher', 5)
        with mock.patch.object(gui.os, 'killpg', side_effect=PermissionError('real uid 0')):
            result = gui.terminate_launcher(process)
        self.assertFalse(result['reaped'])
        self.assertIn('real uid 0', result['signal_error'])
        self.assertIn('bounded cleanup', result['error'])


if __name__ == '__main__':
    unittest.main()
