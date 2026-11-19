"""Browser-button integration tests; all downloaded bytes are local fixtures."""

import hashlib
import http.server
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from adi_model_acquirer import browser_download as module
from adi_model_acquirer.status import AcquisitionStatus


MODEL_URL = "https://www.analog.com/media/en/simulation-models/spice-models/AD8605.cir"
FIXTURE = b"* OFFLINE FIXTURE; NOT AN ADI MODEL\n.SUBCKT AD8605 1 2 99 50 28\n.ENDS AD8605\n"


class FakeBrowser:
    calls = []
    current_url = None
    refs = {"e1": {"role": "button", "name": "Accept and Download"}}
    body = FIXTURE

    def __init__(self, *args, **kwargs):
        self.url = ""

    def run(self, *args):
        self.calls.append(args)
        if args[0] == "open":
            self.url = args[1]
        if args[:2] == ("get", "url"):
            return {"url": self.current_url or self.url}
        if args[0] == "snapshot":
            return {"refs": self.refs}
        if args[0] == "download":
            Path(args[2]).write_bytes(self.body)
            return {"path": args[2]}
        return {}

    def close(self):
        self.calls.append(("close",))


class ADIBrowserDownloadTests(unittest.TestCase):
    def setUp(self):
        FakeBrowser.calls = []
        FakeBrowser.current_url = None
        FakeBrowser.refs = {"e1": {"role": "button", "name": "Accept and Download"}}
        FakeBrowser.body = FIXTURE

    def test_without_authorization_no_browser_is_started(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "AgentBrowser") as browser:
            result = module.download_model("AD8605", MODEL_URL, directory)
        browser.assert_not_called()
        self.assertEqual(result.status, AcquisitionStatus.ACCESS_REQUIRED)
        self.assertEqual(result.files, [])

    def test_browser_timeout_defaults_to_180_and_explicit_value_overrides_it(self):
        for explicit, expected in ((None, 180), (90, 90)):
            with self.subTest(timeout=explicit), tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"ADI_BROWSER_TIMEOUT_SECONDS": "180"}
            ), patch.object(module, "AgentBrowser", wraps=FakeBrowser) as browser:
                result = module.download_model("AD8605", MODEL_URL, directory,
                                               accept_license=True, timeout_seconds=explicit)
                self.assertEqual(result.status, AcquisitionStatus.DOWNLOADED)
                self.assertEqual(browser.call_args.args[1], expected)

    def test_authorized_click_downloads_validates_and_hashes_the_model(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "AgentBrowser", FakeBrowser):
            result = module.download_model("AD8605", MODEL_URL, directory, accept_license=True)
            self.assertEqual(result.status, AcquisitionStatus.DOWNLOADED)
            self.assertEqual(result.acquisition_method, "browser_click")
            file = Path(result.files[0])
            self.assertEqual(file.read_bytes(), FIXTURE)
            self.assertEqual(result.sha256[str(file)], hashlib.sha256(FIXTURE).hexdigest())
            evidence = result.model_resources[0]["license_acceptance"]
            self.assertEqual(evidence["button"], "Accept and Download")
            self.assertTrue(evidence["authorized"])
            self.assertIn("clicked_at_utc", evidence)
            self.assertEqual(next(call[1] for call in FakeBrowser.calls if call[0] == "download"), "@e1")
            self.assertLess(FakeBrowser.calls.index(("scrollintoview", "@e1")),
                            next(i for i, call in enumerate(FakeBrowser.calls) if call[0] == "download"))
            self.assertEqual(FakeBrowser.calls[-1], ("close",))

    def test_an_unexpected_page_is_never_clicked(self):
        FakeBrowser.current_url = "https://www.analog.com/en/login.html"
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "AgentBrowser", FakeBrowser):
            result = module.download_model("AD8605", MODEL_URL, directory, accept_license=True)
        self.assertEqual(result.status, AcquisitionStatus.ERROR)
        self.assertFalse(any(call[0] == "download" for call in FakeBrowser.calls))
        self.assertEqual(FakeBrowser.calls[-1], ("close",))

    def test_missing_accept_button_does_not_click_close_or_another_control(self):
        FakeBrowser.refs = {"e1": {"role": "button", "name": "Close"}}
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "AgentBrowser", FakeBrowser):
            result = module.download_model("AD8605", MODEL_URL, directory, accept_license=True)
        self.assertEqual(result.status, AcquisitionStatus.ERROR)
        self.assertFalse(any(call[0] == "download" for call in FakeBrowser.calls))

    def test_html_is_not_saved_as_a_successful_model(self):
        FakeBrowser.body = b"<html>Download unavailable</html>"
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "AgentBrowser", FakeBrowser):
            result = module.download_model("AD8605", MODEL_URL, directory, accept_license=True)
        self.assertEqual(result.status, AcquisitionStatus.ERROR)
        self.assertEqual(result.files, [])

    def test_wrong_part_model_does_not_pass(self):
        FakeBrowser.body = b".SUBCKT AD620 1 2 3 4 5\n.ENDS AD620\n"
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "AgentBrowser", FakeBrowser):
            result = module.download_model("AD8605", MODEL_URL, directory, accept_license=True)
        self.assertEqual(result.status, AcquisitionStatus.ERROR)
        self.assertEqual(result.files, [])

    def test_browser_profile_attachment_environment_is_not_inherited(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "AGENT_BROWSER_AUTO_CONNECT": "true", "AGENT_BROWSER_PROFILE": "/private/profile",
        }):
            browser = module.AgentBrowser(Path(directory), 10)
        self.assertNotIn("AGENT_BROWSER_AUTO_CONNECT", browser.env)
        self.assertNotIn("AGENT_BROWSER_PROFILE", browser.env)
        self.assertNotIn("--auto-connect", browser.prefix)
        self.assertIn("--session", browser.prefix)

    def test_non_official_or_non_model_urls_are_rejected_before_browser_start(self):
        for url in ("https://example.org/AD8605.cir", MODEL_URL.replace("https:", "http:"),
                    MODEL_URL + "?bypass=true", "https://www.analog.com/en/products/ad8605.html"):
            with self.subTest(url=url), patch.object(module, "AgentBrowser") as browser, self.assertRaises(ValueError):
                module.download_model("AD8605", url, "unused", accept_license=True)
            browser.assert_not_called()

    def test_product_page_access_denied_is_reported_as_network_error(self):
        class DeniedBrowser(FakeBrowser):
            def run(self, *args):
                if args[:3] == ("get", "html", "body"):
                    return {"html": "<h1>Access Denied</h1>Reference #18.errors.edgesuite.net"}
                return super().run(*args)

        with tempfile.TemporaryDirectory() as directory, patch.object(module, "AgentBrowser", DeniedBrowser):
            result = module.download_from_product_page(
                "AD811", "https://www.analog.com/en/products/ad811.html", directory,
            )
        self.assertEqual(result.status, AcquisitionStatus.ERROR)
        self.assertEqual(result.reason_code.value, "NETWORK_ERROR")
        self.assertIn("denied access", result.reason.lower())

    @unittest.skipUnless(os.getenv("ADI_TEST_REAL_BROWSER") == "1", "opt-in real browser/local fixture test")
    def test_real_browser_click_against_local_license_fixture(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # Reproduce ADI's below-the-fold license button so a click
                # without an explicit scroll cannot start the download.
                body = FIXTURE if self.path == "/model" else b'<html><body><h1>LOCAL LICENSE FIXTURE</h1><div style="height:1500px"></div><input type="button" value="Accept and Download" onclick="location.href=\'/model\'"></body></html>'
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream" if self.path == "/model" else "text/html")
                if self.path == "/model":
                    self.send_header("Content-Disposition", 'attachment; filename="AD8605.cir"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        local_license = f"http://127.0.0.1:{server.server_port}/license"
        real_browser = module.AgentBrowser
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(module, "license_url_for", return_value=local_license), patch.object(
                module, "AgentBrowser", side_effect=lambda *args, **kwargs: real_browser(*args, **kwargs, allowed_domains="127.0.0.1")
            ):
                result = module.download_model("AD8605", MODEL_URL, directory, accept_license=True, timeout_seconds=30)
                self.assertEqual(result.status, AcquisitionStatus.DOWNLOADED, result.reason)
                self.assertEqual(Path(result.files[0]).read_bytes(), FIXTURE)
                self.assertEqual(result.sha256[result.files[0]], hashlib.sha256(FIXTURE).hexdigest())
                self.assertEqual(result.model_resources[0]["license_acceptance"]["license_page"], local_license)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
