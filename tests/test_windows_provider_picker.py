"""Keep the Windows provider menu navigable without a curses installation."""

import ast
import unittest
from enum import Enum
from types import SimpleNamespace
from unittest.mock import Mock, patch

from distribution.scripts.assemble import patch_windows_radio_picker


class _Event(Enum):
    CANCEL = 'cancel'


class WindowsProviderPickerTests(unittest.TestCase):
    def _picker(self):
        source = '''def _radio_numbered_fallback(title, items, selected, cancel_returns, *, headings=None):
    """Text-based numbered fallback for radio selection (section titles printed, never numbered)."""
    print(color(f"\\n  {title}", Colors.YELLOW))
'''
        patched = patch_windows_radio_picker(source, 'hermes_cli/curses_ui.py')
        module = ast.parse(patched)
        function = module.body[0]
        namespace = {
            'sys': SimpleNamespace(platform='win32'),
            'radio_item_plain': lambda item: item if isinstance(item, str) else ''.join(part for part, _ in item),
            'MenuNavigationEvent': _Event,
            '_notify_scoped_navigation': Mock(),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<picker>', 'exec'), namespace)
        return namespace

    def test_windows_picker_scrolls_to_selected_index(self):
        namespace = self._picker()
        dialog = Mock()
        dialog.run.return_value = 7
        items = [f'Provider {i}' for i in range(42)]
        items[7] = [('OpenAI', 'yellow')]
        with patch('prompt_toolkit.shortcuts.radiolist_dialog', return_value=dialog) as create_dialog:
            result = namespace['_radio_numbered_fallback']('Select provider:', items, 0, -1)
        self.assertEqual(result, 7)
        values = create_dialog.call_args.kwargs['values']
        self.assertEqual(len(values), 42)
        self.assertEqual(values[7], (7, 'OpenAI'))
        self.assertEqual(create_dialog.call_args.kwargs['default'], 0)

    def test_windows_cancel_preserves_menu_navigation(self):
        namespace = self._picker()
        dialog = Mock()
        dialog.run.return_value = None
        with patch('prompt_toolkit.shortcuts.radiolist_dialog', return_value=dialog):
            result = namespace['_radio_numbered_fallback']('Select provider:', ['OpenAI'], 0, -1)
        self.assertEqual(result, -1)
        namespace['_notify_scoped_navigation'].assert_called_once_with(_Event.CANCEL)


if __name__ == '__main__':
    unittest.main()
