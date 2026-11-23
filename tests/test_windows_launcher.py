"""Keep Windows Command Prompt attached to the runtime and first-run provider flow."""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / 'bin' / 'eda'
loader = SourceFileLoader('agent_eda_launcher_under_test', str(LAUNCHER))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


class WindowsLauncherTests(unittest.TestCase):
    def test_windows_runtime_handoff_waits_and_inherits_terminal(self):
        runtime = Path('C:/agent-eda/runtime/Scripts/python.exe')
        with patch.object(launcher.subprocess, 'run') as run, patch.object(launcher.os, 'execv') as execv:
            run.return_value.returncode = 7
            result = launcher.launch_runtime(runtime, ['model'], windows=True)
        self.assertEqual(result, 7)
        run.assert_called_once_with([str(runtime), str(LAUNCHER), 'model'], check=False)
        execv.assert_not_called()

    def test_fresh_chat_runs_provider_picker_before_chat(self):
        seen = []
        main = Mock(side_effect=lambda: seen.append(list(launcher.sys.argv)))
        configured = Mock(side_effect=[False, True])
        with patch.object(launcher.sys, 'argv', [str(LAUNCHER)]):
            result = launcher.start_agent(main, configured)
        self.assertEqual(result, 0)
        self.assertEqual(main.call_count, 2)
        self.assertEqual(seen, [[str(LAUNCHER), 'model'], [str(LAUNCHER)]])
        self.assertEqual(configured.call_count, 2)

    def test_cancelled_provider_setup_does_not_enter_hermes_setup(self):
        main = Mock(return_value=None)
        with patch.object(launcher.sys, 'argv', [str(LAUNCHER)]):
            result = launcher.start_agent(main, lambda: False)
        self.assertEqual(result, 1)
        main.assert_called_once()


if __name__ == '__main__':
    unittest.main()
