from __future__ import annotations

import hashlib
import io
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from nxp_model_acquirer.service import HttpResponse, NXPModelAcquirer
from nxp_model_acquirer.status import AcquisitionStatus, ReasonCode


PRODUCT = "https://www.nxp.com/products/BF862"
RAW_MODEL = "https://www.nxp.com/downloads/en/spice-model/BF862.prm"
MODEL_BYTES = b"* NXP BF862 model\n.MODEL BF862 NJF(Beta=1m)\n"


def model_zip(member: str = "models/BFU5XX.lib") -> bytes:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(member, ".SUBCKT BFU5XX 1 2 3\n.ENDS BFU5XX\n")
        archive.writestr("documentation/readme.pdf", b"not a model")
    return content.getvalue()


class NXPModelAcquirerTests(unittest.TestCase):
    def test_downloads_public_raw_prm_from_exact_product_page(self):
        html = '<h4>BF862 SPICE model</h4><a href="/downloads/en/spice-model/BF862.prm">Download</a>'
        calls = []

        def fetcher(url):
            calls.append(url)
            if url == PRODUCT:
                return HttpResponse(200, url, html.encode(), "text/html")
            self.assertEqual(RAW_MODEL, url)
            return HttpResponse(200, url, MODEL_BYTES, "application/octet-stream")

        with tempfile.TemporaryDirectory() as directory:
            result = NXPModelAcquirer(directory, fetcher).acquire("bf862", "spice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual([PRODUCT, RAW_MODEL], calls)
            self.assertEqual(MODEL_BYTES, Path(result.files[0]).read_bytes())
            self.assertEqual(hashlib.sha256(MODEL_BYTES).hexdigest(), result.sha256[result.files[0]])
            self.assertTrue(result.exact_part_model_found)

    def test_downloads_and_safely_extracts_family_zip(self):
        model_url = "https://www.nxp.com/downloads/en/design-support/BFU5XX_SPICE.zip"
        html = f'<h3>BFU5xx family SPICE GP model v2</h3><a href="{model_url}">Download</a>'

        def fetcher(url):
            if "/products/" in url:
                return HttpResponse(200, url, html.encode())
            return HttpResponse(200, url, model_zip(), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = NXPModelAcquirer(directory, fetcher).acquire("BFU520", "pspice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual(1, len(result.files))
            self.assertEqual(".lib", Path(result.files[0]).suffix)
            self.assertEqual(1, result.series_model_count)

    def test_uses_heading_for_download_title_and_excludes_s_parameters(self):
        s2p = "https://www.nxp.com/downloads/en/models/BF862.s2p"
        html = f'''<h4>BF862 S-parameters</h4><a href="{s2p}">Download</a>
        <h4>BF862 SPICE model</h4><a href="{RAW_MODEL}">Download</a>'''
        resources, _ = NXPModelAcquirer._model_resources(html, PRODUCT, "spice")
        self.assertEqual([RAW_MODEL], [item.url for item in resources])
        self.assertEqual("BF862 SPICE model", resources[0].title)

    def test_finds_model_resource_in_nested_json(self):
        page = (
            '{"downloads":[{"documentTitle":"BF862 SPICE model",'
            '"downloadUrl":"/downloads/en/spice-model/BF862.prm"}]}'
        )
        resources, _ = NXPModelAcquirer._model_resources(page, PRODUCT, "spice")
        self.assertEqual([RAW_MODEL], [item.url for item in resources])

    def test_simplis_request_only_selects_simplis_resource(self):
        simplis = "https://www.nxp.com/webapp/Download?colCode=PF0300_SIMPLIS.zip"
        html = (
            f'<h4>PF0300 SIMPLIS model</h4><a href="{simplis}">Download</a>'
            f'<h4>PF0300 SPICE model</h4><a href="{RAW_MODEL}">Download</a>'
        )
        resources, _ = NXPModelAcquirer._model_resources(html, PRODUCT, "simplis")
        self.assertEqual([simplis], [item.url for item in resources])

    def test_reports_account_required_without_attempting_a_download(self):
        def fetcher(url):
            return HttpResponse(200, url, b"<h3>PF0300 SIMPLIS Model - Account Required</h3>")

        result = NXPModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("PF0300", "simplis")
        self.assertEqual(AcquisitionStatus.ACCESS_REQUIRED, result.status)
        self.assertEqual(ReasonCode.LOGIN_REQUIRED, result.reason_code)

    def test_reports_login_interstitial_instead_of_saving_html(self):
        login = b"<html><title>Sign in to download</title></html>"

        def fetcher(url):
            if "/products/" in url:
                return HttpResponse(200, url, f'<h4>BF862 SPICE model</h4><a href="{RAW_MODEL}">Download</a>'.encode())
            return HttpResponse(200, url, login, "text/html")

        result = NXPModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("BF862", "spice")
        self.assertEqual(AcquisitionStatus.ACCESS_REQUIRED, result.status)
        self.assertEqual(ReasonCode.LOGIN_REQUIRED, result.reason_code)
        self.assertEqual([], result.files)

    def test_reports_nxp_edge_block_page(self):
        body = b"<html>Sorry! This page is not available. Contact your administrator with the error code.</html>"
        result = NXPModelAcquirer(
            tempfile.mkdtemp(), lambda url: HttpResponse(404, url, body, "text/html")
        ).acquire("BF862", "spice")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.DOWNLOAD_BLOCKED, result.reason_code)

    def test_reports_model_not_published_without_guessing_a_url(self):
        result = NXPModelAcquirer(
            tempfile.mkdtemp(), lambda url: HttpResponse(200, url, b"<h1>BF862 product</h1>")
        ).acquire("BF862", "spice")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.MODEL_NOT_PUBLISHED, result.reason_code)

    def test_reports_requested_format_not_available(self):
        result = NXPModelAcquirer(
            tempfile.mkdtemp(),
            lambda url: HttpResponse(200, url, b"<h3>BF862 SPICE model</h3>"),
        ).acquire("BF862", "simplis")
        self.assertEqual(ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE, result.reason_code)

    def test_reports_part_not_found(self):
        result = NXPModelAcquirer(
            tempfile.mkdtemp(), lambda url: HttpResponse(404, url, b"")
        ).acquire("NO-SUCH-PART", "spice")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.PART_NOT_FOUND, result.reason_code)

    def test_reports_paid_download(self):
        def fetcher(url):
            if "/products/" in url:
                return HttpResponse(200, url, f'<h4>BF862 SPICE model</h4><a href="{RAW_MODEL}">Download</a>'.encode())
            return HttpResponse(402, url, b"")

        result = NXPModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("BF862", "spice")
        self.assertEqual(AcquisitionStatus.PAID, result.status)
        self.assertEqual(ReasonCode.PAID_RESOURCE, result.reason_code)

    def test_rejects_zip_path_traversal(self):
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../outside.lib", ".SUBCKT BAD 1 2\n.ENDS BAD\n")

        def fetcher(url):
            if "/products/" in url:
                return HttpResponse(200, url, f'<h4>BF862 SPICE model</h4><a href="{RAW_MODEL}">Download</a>'.encode())
            return HttpResponse(200, url, unsafe.getvalue(), "application/zip")

        result = NXPModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("BF862", "spice")
        self.assertEqual(ReasonCode.INVALID_DOWNLOAD, result.reason_code)

    def test_rejects_zip_symlink(self):
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            info = zipfile.ZipInfo("model.lib")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")

        def fetcher(url):
            if "/products/" in url:
                return HttpResponse(200, url, f'<h4>BF862 SPICE model</h4><a href="{RAW_MODEL}">Download</a>'.encode())
            return HttpResponse(200, url, unsafe.getvalue(), "application/zip")

        result = NXPModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("BF862", "spice")
        self.assertEqual(ReasonCode.INVALID_DOWNLOAD, result.reason_code)

    def test_non_nxp_fetch_is_rejected_at_network_boundary(self):
        def fetcher(_url):
            return NXPModelAcquirer._fetch("https://example.com/model.lib")

        result = NXPModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("BF862", "spice")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.NETWORK_ERROR, result.reason_code)

    def test_reports_third_party_model_without_fetching_it(self):
        offsite = "https://models.example.org/BF862.lib"
        calls = []

        def fetcher(url):
            calls.append(url)
            return HttpResponse(200, url, f'<h4>BF862 SPICE model</h4><a href="{offsite}">Download</a>'.encode())

        result = NXPModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("BF862", "spice")
        self.assertEqual([PRODUCT], calls)
        self.assertEqual(AcquisitionStatus.ACCESS_REQUIRED, result.status)
        self.assertEqual(ReasonCode.THIRD_PARTY_PROVIDER_REQUIRED, result.reason_code)

    def test_rejects_unknown_asset_type(self):
        result = NXPModelAcquirer(tempfile.mkdtemp(), lambda url: None).acquire("BF862", "ibis")
        self.assertEqual(ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE, result.reason_code)


if __name__ == "__main__":
    unittest.main()
