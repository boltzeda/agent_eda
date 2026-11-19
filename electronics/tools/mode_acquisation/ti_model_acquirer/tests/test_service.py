from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from ti_model_acquirer.service import HttpResponse, TIModelAcquirer
from ti_model_acquirer.status import AcquisitionStatus, ReasonCode


def model_zip() -> bytes:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("lm741.lib", ".SUBCKT LM741 1 2 99 50 28\n.ENDS LM741\n")
    return content.getvalue()


class TIModelAcquirerTests(unittest.TestCase):
    def test_downloads_model_referenced_by_ti_product_page(self):
        product_url = "https://www.ti.com/product/LM741"
        model_url = "https://www.ti.com/lit/zip/SNOM211"
        html = f'<a href="{model_url}" data-navtitle="LM741 PSpice Model">model</a>'

        def fetcher(url):
            if url == product_url:
                return HttpResponse(200, url, html.encode())
            self.assertEqual(model_url, url)
            return HttpResponse(200, url, model_zip(), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = TIModelAcquirer(directory, fetcher).acquire("lm741", "pspice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual(["LM741"], [Path(path).stem.upper() for path in result.files])
            self.assertTrue(Path(result.files[0]).is_file())

    def test_reports_no_published_model_without_guessing(self):
        def fetcher(url):
            return HttpResponse(200, url, b"<h1>Example TI product</h1>")

        result = TIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("ABC123", "pspice")
        self.assertEqual(AcquisitionStatus.UNAVAILABLE, result.status)
        self.assertEqual(ReasonCode.MODEL_NOT_PUBLISHED, result.reason_code)

    def test_reports_third_party_cad_resource(self):
        def fetcher(url):
            return HttpResponse(200, url, b"CAD symbols, footprints & 3D models: Ultra Librarian")

        result = TIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("ABC123", "pspice")
        self.assertEqual(AcquisitionStatus.ACCESS_REQUIRED, result.status)
        self.assertEqual(ReasonCode.THIRD_PARTY_PROVIDER_REQUIRED, result.reason_code)

    def test_reports_paid_download(self):
        product_url = "https://www.ti.com/product/LM741"
        model_url = "https://www.ti.com/lit/zip/SNOM211"

        def fetcher(url):
            if url == product_url:
                return HttpResponse(200, url, b'<a href="https://www.ti.com/lit/zip/SNOM211" data-navtitle="LM741 PSpice Model">model</a>')
            return HttpResponse(402, model_url, b"")

        result = TIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("LM741", "pspice")
        self.assertEqual(AcquisitionStatus.PAID, result.status)
        self.assertEqual(ReasonCode.PAID_RESOURCE, result.reason_code)

    def test_rejects_zip_path_traversal(self):
        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../outside.lib", ".SUBCKT BAD 1 2\n.ENDS BAD\n")

        def fetcher(url):
            if "/product/" in url:
                return HttpResponse(200, url, b'<a href="https://www.ti.com/lit/zip/SNOM211" data-navtitle="LM741 PSpice Model">model</a>')
            return HttpResponse(200, url, unsafe.getvalue(), "application/zip")

        result = TIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("LM741", "pspice")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.INVALID_DOWNLOAD, result.reason_code)

    def test_rejects_non_ti_redirects(self):
        def fetcher(url):
            return TIModelAcquirer._fetch("https://example.com/not-allowed")

        result = TIModelAcquirer(tempfile.mkdtemp(), fetcher).acquire("LM741", "pspice")
        self.assertEqual(AcquisitionStatus.ERROR, result.status)
        self.assertEqual(ReasonCode.NETWORK_ERROR, result.reason_code)

    def test_selects_pspice_card_and_skips_earlier_ibis_card(self):
        product_url = "https://www.ti.com/product/DAC80501"
        ibis_url = "https://www.ti.com/lit/zip/SBAM420"
        pspice_url = "https://www.ti.com/lit/zip/SBAM485"
        html = f'''<a href="{ibis_url}" data-navtitle="DAC80501 IBIS Model">IBIS</a>
        <a href="{pspice_url}" data-navtitle="DAC80501 PSpice Transient Model">PSpice</a>'''
        calls = []

        def fetcher(url):
            calls.append(url)
            if url == product_url:
                return HttpResponse(200, url, html.encode())
            self.assertEqual(pspice_url, url)
            return HttpResponse(200, url, model_zip(), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = TIModelAcquirer(directory, fetcher).acquire("DAC80501", "pspice")
        self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        self.assertEqual([product_url, pspice_url], calls)
        self.assertEqual(pspice_url, result.download_url)
        self.assertTrue(result.exact_part_model_found)

    def test_downloads_all_pspice_models_from_a_ti_family_page(self):
        product_url = "https://www.ti.com/product/REF5050"
        members = ["REF5010", "REF5020", "REF5025", "REF5030", "REF5045", "REF5050"]
        codes = ["SLIM178", "SLIM160", "SLIM163", "SLIM166", "SLIM172", "SLIM175"]
        model_urls = [f"https://www.ti.com/lit/zip/{code}" for code in codes]
        html = "\n".join(
            f'<a href="{url}" data-navtitle="{member} PSpice Model (Rev. A)">model</a>'
            for member, url in zip(members, model_urls)
        )

        def fetcher(url):
            if url == product_url:
                return HttpResponse(200, url, html.encode())
            return HttpResponse(200, url, model_zip(), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = TIModelAcquirer(directory, fetcher).acquire("REF5050", "spice")
        self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
        self.assertEqual(6, result.series_model_count)
        self.assertTrue(result.exact_part_model_found)
        self.assertEqual(members, [resource["title"].split()[0] for resource in result.model_resources])

    def test_skips_large_non_model_result_files_in_a_vendor_archive(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("DAC80501/dac80501.lib", ".SUBCKT DAC80501 1 2\n.ENDS DAC80501\n")
            archive.writestr("DAC80501/test.dat", b"x" * (26 * 1024 * 1024))
            archive.writestr("DAC80501/test.cir", ".LIB \"../dac80501.lib\"\n.TRAN 1m 10m\n.END\n")
        product_url = "https://www.ti.com/product/DAC80501"
        model_url = "https://www.ti.com/lit/zip/SBAM485"

        def fetcher(url):
            if url == product_url:
                return HttpResponse(200, url, f'<a href="{model_url}" data-navtitle="DAC80501 PSpice Transient Model">model</a>'.encode())
            return HttpResponse(200, url, content.getvalue(), "application/zip")

        with tempfile.TemporaryDirectory() as directory:
            result = TIModelAcquirer(directory, fetcher).acquire("DAC80501", "pspice")
            self.assertEqual(AcquisitionStatus.DOWNLOADED, result.status)
            self.assertEqual(1, len(result.files))
