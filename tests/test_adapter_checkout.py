"""Core adapters retain their reviewed content across Windows Git checkouts."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from distribution.scripts.assemble import checked_adapter_bytes


class AdapterCheckoutTests(unittest.TestCase):
    def test_assembly_uses_utf8_when_system_locale_does_not(self):
        source = Path(__file__).resolve().parents[1]
        reference = Path(os.environ.get('AGENT_EDA_REFERENCE', source.parent / 'upstream/hermes'))
        if not reference.is_dir():
            self.skipTest('pinned Hermes reference is unavailable')
        with tempfile.TemporaryDirectory(prefix='agent-eda-encoding-') as directory:
            product = Path(directory) / 'agent-eda'
            shutil.copytree(source, product, ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
            output = Path(directory) / 'builds' / 'assembled'
            environment = dict(os.environ, PYTHONUTF8='0', PYTHONCOERCECLOCALE='0')
            if os.name != 'nt':
                environment['LC_ALL'] = 'C'
            result = subprocess.run(
                [sys.executable, str(product / 'distribution/scripts/assemble.py'),
                 '--product', str(product), '--reference', str(reference), '--output', str(output)],
                capture_output=True, text=True, encoding='utf-8', errors='replace', env=environment)
            self.assertEqual(result.returncode, 0, result.stderr[-1200:])

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
