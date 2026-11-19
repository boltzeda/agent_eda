"""TI-first discovery, download, and validation without Hermes dependencies."""

from __future__ import annotations

import hashlib
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .status import AcquisitionResult, AcquisitionStatus, ReasonCode


TI_HOSTS = {"ti.com", "www.ti.com"}
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100
MODEL_EXTENSIONS = {".lib", ".cir", ".sub", ".mod", ".sp", ".spi", ".mdl", ".tsm", ".sxsch", ".sxsym", ".sxlib"}
SUPPORTED_ASSETS = {"pspice", "spice", "tina-ti", "simplis"}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    body: bytes
    content_type: str = ""


@dataclass(frozen=True)
class ModelResource:
    """One specific model card explicitly named by TI on a product page."""

    title: str
    url: str


class TIModelAcquirer:
    """Download all public, requested-format TI model cards for a part/family."""

    def __init__(self, output_root: Path | str, fetcher=None):
        self.output_root = Path(output_root)
        self.fetcher = fetcher or self._fetch

    def acquire(self, part_number: str, asset_type: str = "pspice") -> AcquisitionResult:
        part = part_number.strip().upper()
        asset = asset_type.strip().lower()
        product_url = f"https://www.ti.com/product/{part}"
        if asset not in SUPPORTED_ASSETS:
            return self._result(AcquisitionStatus.UNAVAILABLE, part, asset,
                                ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE,
                                f"The standalone acquirer does not support '{asset_type}'.", product_url)
        try:
            page = self.fetcher(product_url)
        except urllib.error.HTTPError as exc:
            return self._http_error(part, asset, product_url, exc.code)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return self._result(AcquisitionStatus.ERROR, part, asset, ReasonCode.NETWORK_ERROR,
                                f"TI product-page lookup failed: {exc}", product_url)
        if page.status == 404:
            return self._result(AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                                "TI did not return a product page for this exact part number.", product_url)
        if page.status != 200:
            return self._http_error(part, asset, product_url, page.status)

        html = page.body.decode("utf-8", errors="replace")
        candidates = self._model_resources(html, page.url, asset)
        if not candidates:
            return self._no_candidate_result(part, asset, product_url, html)

        saved_files: list[Path] = []
        resources: list[dict] = []
        errors: list[dict] = []
        for candidate in candidates:
            try:
                payload = self.fetcher(candidate.url)
                if payload.status != 200:
                    errors.append(self._error_record(candidate, self._http_error(
                        part, asset, product_url, payload.status, candidate.url)))
                    continue
                files = self._store_and_extract(part, asset, payload.body, candidate.url)
            except urllib.error.HTTPError as exc:
                errors.append(self._error_record(candidate, self._http_error(
                    part, asset, product_url, exc.code, candidate.url)))
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, zipfile.BadZipFile) as exc:
                errors.append({"title": candidate.title, "download_url": candidate.url,
                               "reason_code": ReasonCode.INVALID_DOWNLOAD.value,
                               "reason": f"TI model archive could not be validated: {exc}"})
                continue
            saved_files.extend(files)
            resources.append({"title": candidate.title, "download_url": candidate.url,
                              "files": [str(path) for path in files]})

        if not saved_files:
            first = errors[0] if errors else {}
            return AcquisitionResult(
                AcquisitionStatus(first.get("status", AcquisitionStatus.ERROR)), part, asset,
                ReasonCode(first.get("reason_code", ReasonCode.INVALID_DOWNLOAD)),
                first.get("reason", "TI model archives could not be validated."),
                product_url, candidates[0].url, resource_errors=errors,
            )

        return AcquisitionResult(
            AcquisitionStatus.DOWNLOADED, part, asset,
            reason=None if not errors else "Some TI model archives failed; see resource_errors.",
            source_url=product_url, download_url=candidates[0].url,
            files=[str(path) for path in saved_files],
            sha256={str(path): self._sha256(path) for path in saved_files},
            model_resources=resources,
            exact_part_model_found=any(self._title_matches_part(item.title, part) for item in candidates),
            series_model_count=len(resources), resource_errors=errors,
        )

    @staticmethod
    def _fetch(url: str) -> HttpResponse:
        requested_host = (urlparse(url).hostname or "").lower()
        if requested_host not in TI_HOSTS:
            raise ValueError("only official ti.com download URLs are allowed")
        request = urllib.request.Request(url, headers={"User-Agent": "ti-model-acquirer/1.1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            resolved_host = (urlparse(response.url).hostname or "").lower()
            if resolved_host not in TI_HOSTS:
                raise ValueError("TI download redirected to a non-TI host")
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(body) > MAX_DOWNLOAD_BYTES:
                raise ValueError("response exceeds the 25 MiB limit")
            return HttpResponse(response.status, response.url, body, response.headers.get_content_type())

    def _store_and_extract(self, part: str, asset: str, data: bytes, source_url: str) -> list[Path]:
        target = self.output_root / part / asset
        target.mkdir(parents=True, exist_ok=True)
        archive_id = Path(urlparse(source_url).path).name
        archive = target / f"{archive_id}.zip"
        archive.write_bytes(data)
        if not zipfile.is_zipfile(archive):
            raise ValueError("expected a ZIP model archive")
        extract_root = target / "extracted" / archive_id
        if extract_root.exists():
            shutil.rmtree(extract_root)
        extract_root.mkdir(parents=True)
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("archive contains too many members")
            for info in infos:
                member = Path(info.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError("archive contains an unsafe path")
                # Keep only actual model artifacts. TI PSpice bundles often
                # include enormous transient-result .dat files that are not
                # model files and are neither needed nor safe to extract.
                if info.is_dir() or member.suffix.lower() not in MODEL_EXTENSIONS:
                    continue
                if info.file_size > MAX_DOWNLOAD_BYTES:
                    raise ValueError("archive member exceeds the size limit")
                destination = extract_root / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        files = sorted(path for path in extract_root.rglob("*")
                       if path.is_file() and self._is_model_file(path))
        if not files:
            raise ValueError("archive contains no recognized simulation-model file")
        return files

    @staticmethod
    def _is_model_file(path: Path) -> bool:
        """Exclude PSpice-generated test netlists from an otherwise valid ZIP."""
        suffix = path.suffix.lower()
        if suffix not in MODEL_EXTENSIONS:
            return False
        if suffix in {".tsm", ".sxsch", ".sxsym", ".sxlib"}:
            return True
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:2 * 1024 * 1024]
        except OSError:
            return False
        return bool(re.search(r"(?im)^\s*\.(?:subckt|model)\b", text))

    @staticmethod
    def _model_resources(html: str, page_url: str, asset: str) -> list[ModelResource]:
        """Use TI's per-card data-navtitle, not broad nearby page wording."""
        pattern = re.compile(
            r'<a\b(?=[^>]*\bhref="(?P<url>[^"]+)")(?=[^>]*\bdata-navtitle="(?P<title>[^"]+)")[^>]*>',
            re.I,
        )
        resources: list[ModelResource] = []
        seen: set[str] = set()
        for match in pattern.finditer(html):
            url = urljoin(page_url, match.group("url"))
            if "/lit/zip/" not in url.lower() or url in seen:
                continue
            title = re.sub(r"\s+", " ", match.group("title")).strip()
            if TIModelAcquirer._title_matches_asset(title, asset):
                resources.append(ModelResource(title, url))
                seen.add(url)
        return resources

    @staticmethod
    def _title_matches_asset(title: str, asset: str) -> bool:
        title = title.lower()
        if asset in {"pspice", "spice"}:
            return "pspice" in title and "ibis" not in title
        if asset == "tina-ti":
            return "tina" in title
        return "simplis" in title

    @staticmethod
    def _title_matches_part(title: str, part: str) -> bool:
        return re.search(rf"(?<![A-Z0-9]){re.escape(part)}(?![A-Z0-9])", title.upper()) is not None

    def _no_candidate_result(self, part: str, asset: str, product_url: str, html: str) -> AcquisitionResult:
        text = re.sub(r"\s+", " ", html).lower()
        if "ultra librarian" in text or "third-party" in text:
            return self._result(AcquisitionStatus.ACCESS_REQUIRED, part, asset,
                                ReasonCode.THIRD_PARTY_PROVIDER_REQUIRED,
                                "TI directs this CAD resource to a third-party provider; no third-party download was attempted.", product_url)
        if any(phrase in text for phrase in ("sign in to download", "login to download", "log in to download")):
            return self._result(AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LOGIN_REQUIRED,
                                "TI indicates that downloading this model requires an authorized login.", product_url)
        if "simulation model" in text or "pspice" in text or "spice model" in text:
            return self._result(AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE,
                                "TI lists simulation resources but not a public model in the requested format.", product_url)
        return self._result(AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.MODEL_NOT_PUBLISHED,
                            "TI's product page does not list a public model in the requested format.", product_url)

    def _http_error(self, part: str, asset: str, product_url: str, status: int, download_url: str | None = None) -> AcquisitionResult:
        if status == 401:
            return self._result(AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LOGIN_REQUIRED,
                                "TI requires an authorized login for this resource.", product_url, download_url)
        if status == 402:
            return self._result(AcquisitionStatus.PAID, part, asset, ReasonCode.PAID_RESOURCE,
                                "The requested model resource requires payment.", product_url, download_url)
        if status == 403:
            return self._result(AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LICENSE_ACCEPTANCE_REQUIRED,
                                "TI denied the download; it may require license acceptance or authorized access.", product_url, download_url)
        if status == 404:
            return self._result(AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                                "TI did not return the requested resource.", product_url, download_url)
        return self._result(AcquisitionStatus.ERROR, part, asset, ReasonCode.DOWNLOAD_BLOCKED,
                            f"TI returned HTTP {status} for the requested resource.", product_url, download_url)

    @staticmethod
    def _error_record(candidate: ModelResource, result: AcquisitionResult) -> dict:
        return {"title": candidate.title, "download_url": candidate.url,
                "status": result.status.value,
                "reason_code": result.reason_code.value if result.reason_code else None,
                "reason": result.reason}

    @staticmethod
    def _result(status, part, asset, code, reason, source_url, download_url=None) -> AcquisitionResult:
        return AcquisitionResult(status, part, asset, code, reason, source_url, download_url)

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
