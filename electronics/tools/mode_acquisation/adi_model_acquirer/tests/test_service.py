from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from adi_model_acquirer.service import HttpResponse, ADIModelAcquirer
from adi_model_acquirer.status import AcquisitionStatus, ReasonCode


def product_url(part: str) -> str:
    return f"https://www.analog.com/en/products/{part.upper()}.html"


def model_url(name: str) -> str:
    return f"https://www.analog.com/media/en/simulation-models/spice-models/{name}"


def raw_model(part: str) -> bytes:
    return f"* {part} macromodel\n.SUBCKT {part} 1 2 3 4 5\n.ENDS {part}\n".encode()


def model_zip(member: str = "ad8605.lib", part: str = "AD8605") -> bytes:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(member, f".SUBCKT {part} 1 2 99 50 28\n.ENDS {part}\n")
    return content.getvalue()


def anchor(href: str, title: str) -> str:
    return f'<a href="{href}">{title}</a>'


class ADIModelAcquirerTests(unittest.TestCase):
    def test_downloads_raw_spice_model_referenced_by_adi_product_page(self):
        page = product_url("ad8605")
        body = raw_model("AD8605")
        html = anchor("/media/en/simulation-models/spice-models/ad8605.cir",
                      "AD8605 SPICE Macro-Model")

        def fetcher(url):
            if url == page:
                return HttpResponse(200, url, html.encode())
            self.assertEqual(model_url("ad8605.cir"), url)
            return HttpResponse(200, url, body, "text/plain")

        with tempfile.TemporaryDirectory() as directory:
            result = ADIModelAcquirer(directory, fetcher).acquire("ad8605", "spice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual(1, len(result.files))
            saved = Path(result.files[0])
            self.assertEqual("ad8605.cir", saved.name)
            self.assertEqual(body, saved.read_bytes())  # stored byte-identical
            self.assertTrue(result.exact_part_model_found)

    def test_downloads_zip_model_when_adi_serves_an_archive(self):
        page = product_url("ad8605")
        html = anchor("/media/en/simulation-models/spice-models/ad8605.zip",
                      "AD8605 SPICE Macro-Model")

        def fetcher(url):
            if url == page:
                return HttpResponse(200, url, html.encode())
            return HttpResponse(200, url, model_zip(), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = ADIModelAcquirer(directory, fetcher).acquire("AD8605", "spice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual(["ad8605.lib"], [Path(path).name for path in result.files])
            self.assertIn("extracted", str(Path(result.files[0])))

    def test_reports_no_published_model_without_guessing(self):
        def fetcher(url):
            return HttpResponse(200, url, b"<h1>Example ADI product</h1>")

        result = ADIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("ABC123", "spice")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.MODEL_NOT_PUBLISHED, result.reason_code)

    def test_does_not_mistake_builtin_ltspice_note_for_a_download(self):
        html = b"<div>Models for the following parts are available in LTspice: AD8605</div>"

        def fetcher(url):
            return HttpResponse(200, url, html)

        result = ADIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.MODEL_NOT_PUBLISHED, result.reason_code)
        self.assertIn("LTspice standard library", result.reason)

    def test_does_not_mistake_cad_symbol_links_for_third_party_models(self):
        # Every ADI product page lists Ultra Librarian/SamacSys for CAD symbols;
        # that must never read as "the simulation model is with a third party".
        html = (b"CAD Symbols, Footprints, and 3D Models: Ultra Librarian - SamacSys "
                b"<h1>AD8605</h1>")

        def fetcher(url):
            return HttpResponse(200, url, html)

        result = ADIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.MODEL_NOT_PUBLISHED, result.reason_code)

    def test_reports_third_party_model_only_when_card_links_offsite(self):
        page = product_url("ad8605")
        html = anchor("https://example.org/model.cir", "AD8605 SPICE Macro-Model")
        calls = []

        def fetcher(url):
            calls.append(url)
            return HttpResponse(200, url, html.encode())

        result = ADIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.ACCESS_REQUIRED, result.status)
        self.assertEqual(ReasonCode.THIRD_PARTY_PROVIDER_REQUIRED, result.reason_code)
        self.assertEqual([page], calls)

    def test_reports_license_interstitial_instead_of_invalid_download(self):
        page = product_url("ad8605")
        url = model_url("ad8605.cir")
        html = anchor("/media/en/simulation-models/spice-models/ad8605.cir",
                      "AD8605 SPICE Macro-Model")
        interstitial = (b"<html><head><title>License agreement for Spice Models</title>"
                        b"</head><body>License Agreement</body></html>")

        def fetcher(requested):
            if requested == page:
                return HttpResponse(200, requested, html.encode())
            return HttpResponse(200, requested, interstitial, "text/html")

        result = ADIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.ACCESS_REQUIRED, result.status)
        self.assertEqual(ReasonCode.LICENSE_ACCEPTANCE_REQUIRED, result.reason_code)
        self.assertEqual([], result.files)
        self.assertEqual("LICENSE_ACCEPTANCE_REQUIRED", result.resource_errors[0]["reason_code"])
        self.assertIn(url, result.resource_errors[0]["reason"])

    def test_reports_paid_download(self):
        page = product_url("ad8605")
        url = model_url("ad8605.cir")
        html = anchor("/media/en/simulation-models/spice-models/ad8605.cir",
                      "AD8605 SPICE Macro-Model")

        def fetcher(requested):
            if requested == page:
                return HttpResponse(200, requested, html.encode())
            return HttpResponse(402, url, b"")

        result = ADIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.PAID, result.status)
        self.assertEqual(ReasonCode.PAID_RESOURCE, result.reason_code)

    def test_rejects_zip_path_traversal(self):
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../outside.lib", ".SUBCKT BAD 1 2\n.ENDS BAD\n")

        def fetcher(url):
            if "/products/" in url:
                return HttpResponse(200, url, anchor(
                    "/media/en/simulation-models/spice-models/ad8605.zip",
                    "AD8605 SPICE Macro-Model").encode())
            return HttpResponse(200, url, unsafe.getvalue(), "application/zip")

        result = ADIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.INVALID_DOWNLOAD, result.reason_code)

    def test_rejects_non_adi_redirects(self):
        def fetcher(url):
            return ADIModelAcquirer._fetch("https://example.com/not-allowed")

        result = ADIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.NETWORK_ERROR, result.reason_code)

    def test_selects_spice_card_and_skips_earlier_ibis_card(self):
        page = product_url("ad8605")
        ibis_url = "/media/en/simulation-models/ibis-models/ad8605.ibs"
        spice_url = model_url("ad8605.cir")
        html = (anchor(ibis_url, "AD8605 IBIS Model") +
                anchor("/media/en/simulation-models/spice-models/ad8605.cir",
                       "AD8605 SPICE Macro-Model"))
        calls = []

        def fetcher(url):
            calls.append(url)
            if url == page:
                return HttpResponse(200, url, html.encode())
            self.assertEqual(spice_url, url)
            return HttpResponse(200, url, raw_model("AD8605"), "text/plain")

        with tempfile.TemporaryDirectory() as directory:
            result = ADIModelAcquirer(directory, fetcher).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        self.assertEqual([page, spice_url], calls)
        self.assertEqual(spice_url, result.download_url)
        self.assertTrue(result.exact_part_model_found)

    def test_downloads_all_spice_models_from_an_adi_family_page(self):
        page = product_url("ad8608")
        members = ["AD8605", "AD8606", "AD8608"]
        html = "\n".join(
            anchor(f"/media/en/simulation-models/spice-models/{member.lower()}.cir",
                   f"{member} SPICE Macro-Model")
            for member in members
        )

        def fetcher(url):
            if url == page:
                return HttpResponse(200, url, html.encode())
            name = url.rsplit("/", 1)[-1].removesuffix(".cir").upper()
            return HttpResponse(200, url, raw_model(name), "text/plain")

        with tempfile.TemporaryDirectory() as directory:
            result = ADIModelAcquirer(directory, fetcher).acquire("AD8608", "spice")
        self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        self.assertEqual(3, result.series_model_count)
        self.assertTrue(result.exact_part_model_found)
        self.assertEqual(members, [resource["title"].split()[0] for resource in result.model_resources])

    def test_skips_non_model_result_files_in_a_vendor_archive(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("ad8605/ad8605.lib", ".SUBCKT AD8605 1 2\n.ENDS AD8605\n")
            archive.writestr("ad8605/test.dat", b"x" * (26 * 1024 * 1024))
            archive.writestr("ad8605/test.cir", ".LIB \"../ad8605.lib\"\n.TRAN 1m 10m\n.END\n")
        page = product_url("ad8605")

        def fetcher(url):
            if url == page:
                return HttpResponse(200, url, anchor(
                    "/media/en/simulation-models/spice-models/ad8605.zip",
                    "AD8605 SPICE Macro-Model").encode())
            return HttpResponse(200, url, content.getvalue(), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = ADIModelAcquirer(directory, fetcher).acquire("AD8605", "spice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual(1, len(result.files))

    def test_txt_model_requires_content_sniff(self):
        page = product_url("ad8605")
        html = anchor("/media/en/simulation-models/spice-models/ad8605.txt",
                      "AD8605 SPICE Macro-Model")

        def page_with(body: bytes):
            def fetcher(url):
                if url == page:
                    return HttpResponse(200, url, html.encode())
                return HttpResponse(200, url, body, "text/plain")
            return fetcher

        with tempfile.TemporaryDirectory() as directory:
            result = ADIModelAcquirer(directory, page_with(raw_model("AD8605"))).acquire("AD8605", "spice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        result = ADIModelAcquirer(tempfile.mkdtemp(), page_with(b"hello world")).acquire("AD8605", "spice")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.INVALID_DOWNLOAD, result.reason_code)

    def test_dedupes_same_url_with_and_without_query_string(self):
        page = product_url("ad8605")
        url = model_url("ad8605.cir")
        html = (anchor("/media/en/simulation-models/spice-models/ad8605.cir",
                       "AD8605 SPICE Macro-Model") +
                anchor("/media/en/simulation-models/spice-models/ad8605.cir?modelType=spice-models",
                       "AD8605 SPICE Macro-Model"))
        calls = []

        def fetcher(requested):
            calls.append(requested)
            if requested == page:
                return HttpResponse(200, requested, html.encode())
            return HttpResponse(200, requested, raw_model("AD8605"), "text/plain")

        with tempfile.TemporaryDirectory() as directory:
            result = ADIModelAcquirer(directory, fetcher).acquire("AD8605", "spice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual([page, url], calls)
            self.assertEqual(1, len(result.files))


if __name__ == "__main__":
    unittest.main()
