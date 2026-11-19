from __future__ import annotations

import hashlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from st_model_acquirer.service import HttpResponse, STModelAcquirer
from st_model_acquirer.status import AcquisitionStatus, ReasonCode


def model_zip(member: str = "a6986f.lib", model: str = "A6986F") -> bytes:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(member, f".SUBCKT {model} 1 2 3\n.ENDS {model}\n")
    return content.getvalue()


def search_body(*resources: tuple[str, str]) -> bytes:
    return json.dumps({
        "results": [
            {"resourceTitle": title, "downloadUrl": url}
            for title, url in resources
        ]
    }).encode()


class STModelAcquirerTests(unittest.TestCase):
    def test_product_page_fallback_rejects_untrusted_url(self):
        fetcher = lambda url: HttpResponse(200, url, b'{"response":{"docs":[]}}', "application/json")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                STModelAcquirer(directory, fetcher).acquire("TSZ121", "pspice", product_url="https://example.com/tsz121.html")

    def test_product_page_fallback_does_not_download_a_different_part(self):
        product = "https://www.st.com/en/amplifiers-and-comparators/tsz121.html"
        other = "https://www.st.com/resource/en/spice_model/tsz122-pspice.zip"
        def fetcher(url):
            if url == product:
                return HttpResponse(200, url, f'<a href="{other}">TSZ122 PSpice model</a>'.encode(), "text/html")
            if "/bin/st/search/resources?" in url:
                return HttpResponse(200, url, b'{"response":{"docs":[]}}', "application/json")
            self.fail(f"Unexpected download: {url}")
        with tempfile.TemporaryDirectory() as directory:
            result = STModelAcquirer(directory, fetcher).acquire("TSZ121", "pspice", product_url=product)
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)

    def test_product_page_fallback_downloads_exact_part_model(self):
        product = "https://www.st.com/en/amplifiers-and-comparators/tsz121.html"
        model = "https://www.st.com/resource/en/spice_model/tsz121-pspice-model-v2.zip"

        def fetcher(url):
            if "/bin/st/search/resources?" in url:
                return HttpResponse(200, url, b'{"response":{"docs":[]}}', "application/json")
            if url == product:
                return HttpResponse(200, url, f'<a href="{model}">TSZ121 PSpice model</a>'.encode(), "text/html")
            if url == model:
                return HttpResponse(200, url, model_zip("TSZ121.lib", "TSZ121"), "application/zip")
            self.fail(f"Unexpected request: {url}")

        with tempfile.TemporaryDirectory() as directory:
            result = STModelAcquirer(directory, fetcher).acquire("TSZ121", "pspice", product_url=product)
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual(model, result.download_url)
            self.assertTrue(Path(result.files[0]).is_file())

    def test_downloads_zip_referenced_by_st_resource_index(self):
        model_url = "https://www.st.com/resource/en/spice_model/a6986f_pspice_model.zip"
        calls: list[str] = []

        def fetcher(url):
            calls.append(url)
            if "/bin/st/search/resources?" in url:
                return HttpResponse(
                    200, url,
                    search_body(("A6986F Pspice model", model_url)),
                    "application/json",
                )
            self.assertEqual(model_url, url)
            return HttpResponse(200, url, model_zip(), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = STModelAcquirer(directory, fetcher).acquire("a6986f", "pspice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual(1, len(result.files))
            self.assertTrue(Path(result.files[0]).is_file())
            self.assertTrue(result.exact_part_model_found)
            self.assertIn("q=A6986F", calls[0])

    def test_downloads_raw_mod_file_byte_identical(self):
        model_url = (
            "https://www.st.com/resource/en/spice_model/"
            "tsc101a_b_c_macromodel__pspicenewpinsorder.mod"
        )
        body = b"* ST macromodel\n.MODEL INPUT_STAGE NPN(IS=1e-14)\n"

        def fetcher(url):
            if "/bin/st/search/resources?" in url:
                return HttpResponse(
                    200, url,
                    search_body(("TSC101 A B C macromodel PSPICE", model_url)),
                    "application/json",
                )
            return HttpResponse(200, url, body, "video/mpeg")

        with tempfile.TemporaryDirectory() as directory:
            result = STModelAcquirer(directory, fetcher).acquire("TSC101", "spice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            saved = Path(result.files[0])
            self.assertEqual(body, saved.read_bytes())
            self.assertEqual(hashlib.sha256(body).hexdigest(), result.sha256[str(saved)])

    def test_filters_resources_by_requested_asset_type(self):
        pspice = "https://www.st.com/resource/en/spice_model/a6986f_pspice_model.zip"
        simplis = "https://www.st.com/resource/en/simplis_model/a6986f-simplis.zip"
        calls: list[str] = []

        def fetcher(url):
            calls.append(url)
            if "/bin/st/search/resources?" in url:
                return HttpResponse(
                    200, url,
                    search_body(
                        ("A6986F Pspice model", pspice),
                        ("A6986F SIMPLIS examples schematics", simplis),
                    ),
                    "application/json",
                )
            self.assertEqual(simplis, url)
            return HttpResponse(200, url, model_zip("model.sxlib"), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = STModelAcquirer(directory, fetcher).acquire("A6986F", "simplis")
        self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        self.assertEqual([simplis], calls[1:])

    def test_ltspice_does_not_select_pspice_resource(self):
        pspice = "https://www.st.com/resource/en/spice_model/ldp01y_pspice.zip"
        ltspice = "https://www.st.com/resource/en/spice_model/ldp01ty_ltspice.zip"

        def fetcher(url):
            if "/bin/st/search/resources?" in url:
                return HttpResponse(
                    200, url,
                    search_body(
                        ("ldp01y-series PSpice model", pspice),
                        ("ldp01ty-series model for LTspice", ltspice),
                    ),
                    "application/json",
                )
            self.assertEqual(ltspice, url)
            return HttpResponse(200, url, model_zip("ldp01.lib", "LDP01"))

        with tempfile.TemporaryDirectory() as directory:
            result = STModelAcquirer(directory, fetcher).acquire("LDP01-68AY", "ltspice")
        self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        self.assertEqual(ltspice, result.download_url)

    def test_downloads_all_family_resources(self):
        resources = [
            (
                f"A6986F{suffix} Pspice model",
                f"https://www.st.com/resource/en/spice_model/a6986f{suffix.lower()}_pspice.zip",
            )
            for suffix in ("", "3V3", "5V")
        ]

        def fetcher(url):
            if "/bin/st/search/resources?" in url:
                return HttpResponse(200, url, search_body(*resources), "application/json")
            return HttpResponse(200, url, model_zip())

        with tempfile.TemporaryDirectory() as directory:
            result = STModelAcquirer(directory, fetcher).acquire("A6986F", "pspice")
        self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        self.assertEqual(3, result.series_model_count)
        self.assertEqual(3, len(result.model_resources))

    def test_parses_html_anchor_fallback_and_deduplicates_query_strings(self):
        canonical = "https://www.st.com/resource/en/spice_model/tsc101.mod"
        html = (
            '<a href="/resource/en/spice_model/tsc101.mod">TSC101 PSpice model</a>'
            '<a href="/resource/en/spice_model/tsc101.mod?download=true">duplicate</a>'
        ).encode()
        calls: list[str] = []

        def fetcher(url):
            calls.append(url)
            if "/bin/st/search/resources?" in url:
                return HttpResponse(200, url, html, "text/html")
            return HttpResponse(200, url, b".SUBCKT TSC101 1 2\n.ENDS\n")

        with tempfile.TemporaryDirectory() as directory:
            result = STModelAcquirer(directory, fetcher).acquire("TSC101", "pspice")
        self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        self.assertEqual([canonical], calls[1:])

    def test_reports_no_published_model_without_guessing(self):
        def fetcher(url):
            return HttpResponse(200, url, b'{"results": []}', "application/json")

        result = STModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("NO-SUCH-PART")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.MODEL_NOT_PUBLISHED, result.reason_code)

    def test_reports_requested_format_not_available(self):
        body = search_body((
            "A6986F SIMPLIS examples schematics",
            "https://www.st.com/resource/en/simplis_model/a6986f.zip",
        ))

        def fetcher(url):
            return HttpResponse(200, url, body, "application/json")

        result = STModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("A6986F", "eldo")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE, result.reason_code)

    def test_reports_third_party_model_without_fetching_it(self):
        offsite = "https://models.example.org/a6986f_pspice.zip"
        calls: list[str] = []

        def fetcher(url):
            calls.append(url)
            return HttpResponse(
                200, url,
                search_body(("A6986F PSpice model", offsite)),
                "application/json",
            )

        result = STModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("A6986F", "pspice")
        self.assertEqual(AcquisitionStatus.ACCESS_REQUIRED, result.status)
        self.assertEqual(ReasonCode.THIRD_PARTY_PROVIDER_REQUIRED, result.reason_code)
        self.assertEqual(1, len(calls))

    def test_reports_license_interstitial(self):
        model_url = "https://www.st.com/resource/en/spice_model/restricted.mod"

        def fetcher(url):
            if "/bin/st/search/resources?" in url:
                return HttpResponse(
                    200, url, search_body(("Restricted PSpice model", model_url)),
                    "application/json",
                )
            return HttpResponse(
                200, url,
                b"<html><body>License Agreement - accept the license</body></html>",
                "text/html",
            )

        result = STModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("RESTRICTED")
        self.assertEqual(AcquisitionStatus.ACCESS_REQUIRED, result.status)
        self.assertEqual(ReasonCode.LICENSE_ACCEPTANCE_REQUIRED, result.reason_code)

    def test_reports_paid_download(self):
        model_url = "https://www.st.com/resource/en/spice_model/a6986f.zip"

        def fetcher(url):
            if "/bin/st/search/resources?" in url:
                return HttpResponse(
                    200, url, search_body(("A6986F Pspice model", model_url)),
                    "application/json",
                )
            return HttpResponse(402, url, b"")

        result = STModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("A6986F")
        self.assertEqual(AcquisitionStatus.PAID, result.status)
        self.assertEqual(ReasonCode.PAID_RESOURCE, result.reason_code)

    def test_rejects_zip_path_traversal(self):
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../outside.lib", ".SUBCKT BAD 1 2\n.ENDS\n")
        model_url = "https://www.st.com/resource/en/spice_model/unsafe.zip"

        def fetcher(url):
            if "/bin/st/search/resources?" in url:
                return HttpResponse(
                    200, url, search_body(("Unsafe PSpice model", model_url)),
                    "application/json",
                )
            return HttpResponse(200, url, unsafe.getvalue(), "application/zip")

        result = STModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("UNSAFE")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.INVALID_DOWNLOAD, result.reason_code)

    def test_rejects_zip_symbolic_link(self):
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            info = zipfile.ZipInfo("linked.lib")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target.lib")
        model_url = "https://www.st.com/resource/en/spice_model/unsafe.zip"

        def fetcher(url):
            if "/bin/st/search/resources?" in url:
                return HttpResponse(
                    200, url, search_body(("Unsafe PSpice model", model_url)),
                    "application/json",
                )
            return HttpResponse(200, url, unsafe.getvalue(), "application/zip")

        result = STModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("UNSAFE")
        self.assertEqual(ReasonCode.INVALID_DOWNLOAD, result.reason_code)

    def test_rejects_non_st_urls_at_fetch_boundary(self):
        def fetcher(url):
            return STModelAcquirer._fetch("https://example.com/model.zip")

        result = STModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("A6986F")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.NETWORK_ERROR, result.reason_code)

    def test_invalid_asset_returns_stable_reason(self):
        result = STModelAcquirer(tempfile.mkdtemp()).acquire("A6986F", "ibis")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE, result.reason_code)


if __name__ == "__main__":
    unittest.main()
