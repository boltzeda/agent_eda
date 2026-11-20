"""Core adapters retain their reviewed content across Windows Git checkouts."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from distribution.scripts.assemble import checked_adapter_bytes


class AdapterCheckoutTests(unittest.TestCase):
    def test_crlf_checkout_assembles_as_reviewed_lf_bytes(self):
        reviewed = b'def eda():\n    return "Agent-EDA"\n'
        entry = {'product_sha256': hashlib.sha256(reviewed).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / '_parser.py'
            source.write_bytes(reviewed.replace(b'\n', b'\r\n'))
            self.assertEqual(checked_adapter_bytes(source, entry, 'hermes_cli/_parser.py'), reviewed)
            source.write_bytes(source.read_bytes().replace(b'Agent-EDA', b'Other-EDA'))
            with self.assertRaisesRegex(ValueError, 'core adapter is undeclared or changed'):
                checked_adapter_bytes(source, entry, 'hermes_cli/_parser.py')


if __name__ == '__main__':
    unittest.main()
