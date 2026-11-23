"""The updater smoke check must accept Windows' subprocess handoff."""

from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from distribution.scripts.verify_runtime import verify_update_handoff


class WindowsUpdateHandoffTests(unittest.TestCase):
    def test_smoke_check_uses_windows_subprocess_handoff(self):
        product = Path('C:/agent-eda')

        def relaunch(arguments, *, preserve_inherited):
            self.assertEqual(arguments, ['update'])
            self.assertFalse(preserve_inherited)
            raise SystemExit(subprocess.call(['python.exe', str(product / 'bin/eda'), 'update']))

        with patch('sys.platform', 'win32'):
            verify_update_handoff(product, relaunch)

    def test_smoke_check_rejects_wrong_windows_target(self):
        def relaunch(arguments, *, preserve_inherited):
            raise SystemExit(subprocess.call(['python.exe', 'hermes', 'update']))

        with patch('sys.platform', 'win32'):
            with self.assertRaisesRegex(AssertionError, 'Agent-EDA launcher'):
                verify_update_handoff(Path('C:/agent-eda'), relaunch)


if __name__ == '__main__':
    unittest.main()
