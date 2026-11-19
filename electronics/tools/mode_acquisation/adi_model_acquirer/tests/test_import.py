from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from adi_model_acquirer.service import ADIModelAcquirer
from adi_model_acquirer.status import AcquisitionStatus


SOURCE = "https://www.analog.com/media/en/simulation-models/spice-models/AD8605.cir"
FIXTURE = b"* OFFLINE TEST FIXTURE; not an ADI download\n.SUBCKT AD8605 1 2 99 50 28\n.ENDS AD8605\n"


class ADIImportTests(unittest.TestCase):
    def test_raw_import_preserves_bytes_hash_and_user_supplied_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "browser-download.cir"
            source.write_bytes(FIXTURE)
            fetcher = MagicMock(side_effect=AssertionError("import must not access the network"))
            result = ADIModelAcquirer(root / "models", fetcher).import_model("AD8605", source, SOURCE)
            self.assertEqual(result.status, AcquisitionStatus.IMPORTED)
            self.assertEqual(result.acquisition_method, "manual_import")
            self.assertTrue(result.exact_part_model_found)
            self.assertEqual(result.model_resources[0]["provenance"], "user_supplied")
            saved = Path(result.files[0])
            self.assertEqual(saved.read_bytes(), FIXTURE)
            self.assertEqual(result.sha256[str(saved)], hashlib.sha256(FIXTURE).hexdigest())
            self.assertEqual(source.read_bytes(), FIXTURE)
            fetcher.assert_not_called()

    def test_zip_import_extracts_the_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "download.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("AD8605.lib", FIXTURE)
                archive.writestr("readme.html", "not a model")
            result = ADIModelAcquirer(root / "models").import_model("AD8605", source, SOURCE[:-3] + "zip")
            self.assertEqual(result.status, AcquisitionStatus.IMPORTED)
            self.assertEqual(len(result.files), 1)
            self.assertEqual(Path(result.files[0]).read_bytes(), FIXTURE)

    def test_invalid_sources_and_files_never_return_a_usable_model(self):
        for label, data, url in (
            ("html", b"<html>License agreement for Spice Models</html>", SOURCE),
            ("wrong_part", b".SUBCKT LM741 1 2 3 4 5\n.ENDS LM741\n", SOURCE),
            ("offsite", FIXTURE, "https://example.org/AD8605.cir"),
            ("credentials", FIXTURE, SOURCE.replace("www.analog.com", "user:pass@www.analog.com")),
            ("http", FIXTURE, SOURCE.replace("https:", "http:")),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "download.cir"
                source.write_bytes(data)
                result = ADIModelAcquirer(root / "models").import_model("AD8605", source, url)
                self.assertEqual(result.status, AcquisitionStatus.ERROR)
                self.assertEqual(result.files, [])
                self.assertEqual(result.acquisition_method, "manual_import")

    def test_zip_import_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "download.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../../outside.lib", FIXTURE)
            result = ADIModelAcquirer(root / "models").import_model("AD8605", source, SOURCE[:-3] + "zip")
            self.assertEqual(result.status, AcquisitionStatus.ERROR)
            self.assertFalse((root / "outside.lib").exists())

    def test_cli_import_end_to_end(self):
        plugin_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "download.cir"
            source.write_bytes(FIXTURE)
            command = [sys.executable, "-m", "adi_model_acquirer.cli", "AD8605",
                       "--import-file", str(source), "--source-url", SOURCE,
                       "--output", str(root / "models")]
            completed = subprocess.run(command, cwd=plugin_root, capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "imported")
            self.assertEqual(result["acquisition_method"], "manual_import")
            self.assertEqual(Path(result["files"][0]).read_bytes(), FIXTURE)

    def test_configured_timeout_reaches_http_transport(self):
        response = MagicMock()
        response.url = SOURCE
        response.status = 200
        response.read.return_value = FIXTURE
        response.headers.get_content_type.return_value = "text/plain"
        with patch.dict(os.environ, {"ADI_HTTP_TIMEOUT_SECONDS": "47"}), patch("urllib.request.urlopen") as open_url:
            open_url.return_value.__enter__.return_value = response
            ADIModelAcquirer("unused").fetcher(SOURCE)
            self.assertEqual(open_url.call_args.kwargs["timeout"], 47)

    def test_invalid_timeouts_are_rejected(self):
        for value in (0, -1, 181, float("nan"), float("inf")):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                ADIModelAcquirer("unused", timeout_seconds=value)


if __name__ == "__main__":
    unittest.main()
