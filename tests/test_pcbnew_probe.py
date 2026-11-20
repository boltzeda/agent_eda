"""Setup should use the same KiCad discovery path as the PCB workflow."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from electronics.tools.setup_tool_environments import probe_pcbnew


class PcbnewProbeTests(unittest.TestCase):
    def test_probe_finds_kicad_windows_site_packages(self):
        tools_root = Path(__file__).resolve().parents[1] / 'electronics/tools'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'bin'
            binary.mkdir()
            (binary / '_pcbnew.kiface').write_bytes(b'not a Python module')
            binding = binary / 'Lib' / 'site-packages'
            binding.mkdir(parents=True)
            (binding / 'pcbnew.py').write_text(
                'def GetBuildVersion():\n    return "windows-kicad"\n', encoding='utf-8')
            with patch.dict('os.environ', {'KICAD_ROOT': str(root)}, clear=False):
                self.assertEqual(probe_pcbnew(Path(sys.executable), tools_root), 'windows-kicad')

    def test_probe_finds_binding_beside_kicad_install(self):
        tools_root = Path(__file__).resolve().parents[1] / 'electronics/tools'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binding = root / 'bin'
            binding.mkdir()
            (binding / 'pcbnew.py').write_text(
                'def GetBuildVersion():\n    return "test-kicad"\n', encoding='utf-8')
            with patch.dict('os.environ', {'KICAD_ROOT': str(root)}, clear=False):
                self.assertEqual(probe_pcbnew(Path(sys.executable), tools_root), 'test-kicad')


if __name__ == '__main__':
    unittest.main()
