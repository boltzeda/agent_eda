"""ADI-first discovery, download, and validation without Hermes dependencies."""

from __future__ import annotations

import hashlib
import io
import math
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from .status import AcquisitionResult, AcquisitionStatus, ReasonCode


ADI_HOSTS = {"analog.com", "www.analog.com"}
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100
MODEL_EXTENSIONS = {".lib", ".cir", ".sub", ".mod", ".sp", ".spi", ".mdl", ".tsm",
                    ".sxsch", ".sxsym", ".sxlib", ".txt", ".ibs"}
# Binary/vendor formats whose content cannot be sniffed as text SPICE.
SUFFIX_TRUSTED = {".tsm", ".sxsch", ".sxsym", ".sxlib", ".ibs"}
SUPPORTED_ASSETS = {"spice", "pspice", "ltspice", "simplis", "ibis"}
MEDIA_PATH_MARKER = "/media/en/simulation-models/"
DEMO_CIRCUIT_PATH = "/ltspice-demo-circuits/"
LICENSE_MARKER = "license agreement"
LTSPICE_BUILTIN_MARKER = "available in ltspice"
_LOGIN_PHRASES = ("sign in to download", "log in to download", "login to download",
                  "register to download")
_TITLE_NOISE = ("license", "ultra librarian", "samacsys", "symbol", "footprint",
                "datasheet", "demo circuit", "guide", "3d model")
_SIZE_TOKEN_RE = re.compile(r"\s+\d+(?:\.\d+)?\s*[kmg]?b\s*$", re.I)
_ANCHOR_RE = re.compile(
    r'<a\b(?=[^>]*\bhref=(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\'))[^>]*>(?P<text>.*?)</a>',
    re.I | re.DOTALL,
)
_SNIFF_RE = re.compile(r"(?im)^\s*\.(?:subckt|model)\b")


class LicenseAcceptanceRequired(ValueError):
    """ADI served a license-agreement interstitial instead of the model file."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    body: bytes
    content_type: str = ""


@dataclass(frozen=True)
class ModelResource:
    """One specific model card explicitly named by ADI on a product page."""

    title: str
    url: str


class ADIModelAcquirer:
    """Download all public, requested-format ADI model cards for a part/family."""

    def __init__(self, output_root: Path | str, fetcher=None, *, timeout_seconds: float | None = None):
        self.output_root = Path(output_root)
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None
                                     else os.getenv("ADI_HTTP_TIMEOUT_SECONDS", "30"))
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 180:
            raise ValueError("ADI HTTP timeout must be greater than zero and at most 180 seconds")
        self.fetcher = fetcher or (lambda url: self._fetch(url, self.timeout_seconds))

    def import_model(self, part_number: str, local_file: Path | str,
                     source_url: str, asset_type: str = "spice") -> AcquisitionResult:
        """Validate a supplied local download; its origin is user-declared."""
        part = part_number.strip().upper()
        asset = asset_type.strip().lower()
        product_url = f"https://www.analog.com/en/products/{part}.html"
        try:
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,127}", part):
                raise ValueError("part_number must be an exact ADI part number")
            if asset not in SUPPORTED_ASSETS:
                raise ValueError("unsupported model format")
            parsed = urlparse(source_url)
            if (parsed.scheme != "https" or (parsed.hostname or "").lower() not in ADI_HOSTS
                    or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or not parsed.path.lower().startswith(MEDIA_PATH_MARKER)):
                raise ValueError("source_url must be an official HTTPS analog.com simulation-model URL")
            source = Path(local_file).expanduser().resolve()
            if not source.is_file():
                raise ValueError(f"local model file does not exist: {source}")
            if source.stat().st_size > MAX_DOWNLOAD_BYTES:
                raise ValueError("model file exceeds the 25 MiB limit")
            data = source.read_bytes()
            self.output_root.mkdir(parents=True, exist_ok=True)
            # Validate in staging so rejected imports do not become selected
            # files or overwrite a previously validated model.
            with tempfile.TemporaryDirectory(prefix=".adi-import-", dir=self.output_root) as temporary:
                staged = ADIModelAcquirer(temporary)._store_download(part, asset, data, source_url)
                names = [name for file in staged for name in re.findall(
                    r"(?im)^\s*\.(?:subckt|model)\s+(\S+)", file.read_text(errors="replace"))]
                exact = any(self._title_matches_part(name, part) for name in names)
                if asset != "ibis" and not exact:
                    raise ValueError("SPICE declarations do not match the requested part")
            files = self._store_download(part, asset, data, source_url)
            paths = [str(file.resolve()) for file in files]
            return AcquisitionResult(
                AcquisitionStatus.IMPORTED, part, asset, source_url=product_url, download_url=source_url,
                files=paths, sha256={str(file.resolve()): self._sha256(file) for file in files},
                model_resources=[{"title": f"{part} user-supplied model", "download_url": source_url,
                                  "files": paths, "provenance": "user_supplied",
                                  "source_verified": False, "license_acceptance_verified": False}],
                exact_part_model_found=exact, series_model_count=1, acquisition_method="manual_import",
            )
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            return AcquisitionResult(AcquisitionStatus.ERROR, part, asset, ReasonCode.INVALID_DOWNLOAD,
                                     str(exc), product_url, source_url, acquisition_method="manual_import")

    def acquire(self, part_number: str, asset_type: str = "spice") -> AcquisitionResult:
        part = part_number.strip().upper()
        asset = asset_type.strip().lower()
        product_url = f"https://www.analog.com/en/products/{part}.html"
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
                                f"ADI product-page lookup failed: {exc}", product_url)
        if page.status == 404:
            return self._result(AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                                "ADI did not return a product page for this exact part number.", product_url)
        if page.status != 200:
            return self._http_error(part, asset, product_url, page.status)

        html = page.body.decode("utf-8", errors="replace")
        candidates, offsite = self._model_resources(html, page.url, asset)
        if not candidates:
            return self._no_candidate_result(part, asset, product_url, html, offsite)

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
                files = self._store_download(part, asset, payload.body, candidate.url, payload.content_type)
            except urllib.error.HTTPError as exc:
                errors.append(self._error_record(candidate, self._http_error(
                    part, asset, product_url, exc.code, candidate.url)))
                continue
            except LicenseAcceptanceRequired as exc:
                errors.append({"title": candidate.title, "download_url": candidate.url,
                               "status": AcquisitionStatus.ACCESS_REQUIRED.value,
                               "reason_code": ReasonCode.LICENSE_ACCEPTANCE_REQUIRED.value,
                               "reason": f"{exc} The agreement is never bypassed or auto-accepted."})
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, zipfile.BadZipFile) as exc:
                errors.append({"title": candidate.title, "download_url": candidate.url,
                               "reason_code": ReasonCode.INVALID_DOWNLOAD.value,
                               "reason": f"ADI model file could not be validated: {exc}"})
                continue
            saved_files.extend(files)
            resources.append({"title": candidate.title, "download_url": candidate.url,
                              "files": [str(path) for path in files]})

        if not saved_files:
            first = errors[0] if errors else {}
            return AcquisitionResult(
                AcquisitionStatus(first.get("status", AcquisitionStatus.ERROR)), part, asset,
                ReasonCode(first.get("reason_code", ReasonCode.INVALID_DOWNLOAD)),
                first.get("reason", "ADI model files could not be validated."),
                product_url, candidates[0].url, resource_errors=errors,
            )

        return AcquisitionResult(
            AcquisitionStatus.DOWNLOADED, part, asset,
            reason=None if not errors else "Some ADI model files failed; see resource_errors.",
            source_url=product_url, download_url=candidates[0].url,
            files=[str(path) for path in saved_files],
            sha256={str(path): self._sha256(path) for path in saved_files},
            model_resources=resources,
            exact_part_model_found=any(self._title_matches_part(item.title, part) for item in candidates),
            series_model_count=len(resources), resource_errors=errors,
        )

    @staticmethod
    def _fetch(url: str, timeout_seconds: float = 30) -> HttpResponse:
        requested_host = (urlparse(url).hostname or "").lower()
        if requested_host not in ADI_HOSTS:
            raise ValueError("only official analog.com download URLs are allowed")
        request = urllib.request.Request(url, headers={"User-Agent": "adi-model-acquirer/1.0"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            resolved_host = (urlparse(response.url).hostname or "").lower()
            if resolved_host not in ADI_HOSTS:
                raise ValueError("ADI download redirected to a non-ADI host")
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(body) > MAX_DOWNLOAD_BYTES:
                raise ValueError("response exceeds the 25 MiB limit")
            return HttpResponse(response.status, response.url, body, response.headers.get_content_type())

    def _store_download(self, part: str, asset: str, data: bytes, source_url: str,
                        content_type: str = "") -> list[Path]:
        """Store a downloaded ADI artifact byte-identical; extract only model files."""
        target = self.output_root / part / asset
        target.mkdir(parents=True, exist_ok=True)

        stripped = data.lstrip()[:512].lower()
        if content_type.lower().startswith("text/html") or stripped.startswith((b"<!doctype", b"<html")):
            text = data.decode("utf-8", errors="replace").lower()
            if LICENSE_MARKER in text:
                raise LicenseAcceptanceRequired(
                    "ADI requires accepting its SPICE model license agreement before "
                    f"downloading {source_url}; open the page in a browser to review it.")
            raise ValueError("ADI returned an HTML page instead of a model file")

        if zipfile.is_zipfile(io.BytesIO(data)):
            return self._store_zip(target, data, source_url)
        return [self._store_raw(target, data, source_url)]

    def _store_zip(self, target: Path, data: bytes, source_url: str) -> list[Path]:
        archive_id = self._safe_name(Path(urlparse(source_url).path).name)
        if not archive_id:
            raise ValueError("download URL has no usable filename")
        archive = target / f"{archive_id}.zip"
        archive.write_bytes(data)
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
                # Keep only actual model artifacts; ADI bundles may include
                # documentation or oversized transient-result files.
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

    def _store_raw(self, target: Path, data: bytes, source_url: str) -> Path:
        basename = self._safe_name(Path(unquote(urlparse(source_url).path)).name)
        if not basename:
            raise ValueError("download URL has no usable filename")
        suffix = Path(basename).suffix.lower()
        if suffix not in MODEL_EXTENSIONS:
            raise ValueError(f"'{suffix}' is not a recognized simulation-model extension")
        if suffix not in SUFFIX_TRUSTED:
            text = data.decode("utf-8", errors="replace")[:2 * 1024 * 1024]
            if not _SNIFF_RE.search(text):
                raise ValueError("file does not contain a .SUBCKT/.MODEL definition")
        destination = target / basename
        if destination.exists() and self._sha256(destination) != hashlib.sha256(data).hexdigest():
            # Same filename served from a different URL: keep both, deterministically.
            stem = Path(basename).stem
            destination = target / f"{stem}-{hashlib.sha256(source_url.encode()).hexdigest()[:8]}{suffix}"
        destination.write_bytes(data)
        return destination

    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")
        return cleaned or ""

    @staticmethod
    def _is_model_file(path: Path) -> bool:
        """Exclude PSpice-generated test netlists from an otherwise valid archive."""
        suffix = path.suffix.lower()
        if suffix not in MODEL_EXTENSIONS:
            return False
        if suffix in SUFFIX_TRUSTED:
            return True
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:2 * 1024 * 1024]
        except OSError:
            return False
        return bool(_SNIFF_RE.search(text))

    @staticmethod
    def _model_resources(html: str, page_url: str, asset: str) -> tuple[list[ModelResource], list[ModelResource]]:
        """Use ADI's per-card anchor text and simulation-models media paths."""
        resources: list[ModelResource] = []
        offsite: list[ModelResource] = []
        seen: set[tuple[str, str]] = set()
        for match in _ANCHOR_RE.finditer(html):
            url = urljoin(page_url, match.group("dq") or match.group("sq"))
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group("text"))).strip()
            title = _SIZE_TOKEN_RE.sub("", title).strip()
            if not title or not ADIModelAcquirer._title_matches_asset(title, asset):
                continue
            host = (urlparse(url).hostname or "").lower()
            if host not in ADI_HOSTS:
                offsite.append(ModelResource(title, url))
                continue
            path = urlparse(url).path.lower()
            if MEDIA_PATH_MARKER not in path or DEMO_CIRCUIT_PATH in path:
                continue
            key = (host, path)
            if key in seen:
                continue
            seen.add(key)
            resources.append(ModelResource(title, url))
        return resources, offsite

    @staticmethod
    def _title_matches_asset(title: str, asset: str) -> bool:
        title = title.lower()
        if any(term in title for term in _TITLE_NOISE):
            return False
        if asset in {"spice", "pspice"}:
            return "spice" in title and "ibis" not in title and "simplis" not in title
        if asset == "ltspice":
            return "ltspice" in title and LTSPICE_BUILTIN_MARKER not in title
        if asset == "simplis":
            return "simplis" in title and "ibis" not in title
        return "ibis" in title

    @staticmethod
    def _title_matches_part(title: str, part: str) -> bool:
        return re.search(rf"(?<![A-Z0-9]){re.escape(part)}(?![A-Z0-9])", title.upper()) is not None

    def _no_candidate_result(self, part: str, asset: str, product_url: str, html: str,
                             offsite: list[ModelResource]) -> AcquisitionResult:
        if offsite:
            host = (urlparse(offsite[0].url).hostname or "a third-party site")
            return self._result(AcquisitionStatus.ACCESS_REQUIRED, part, asset,
                                ReasonCode.THIRD_PARTY_PROVIDER_REQUIRED,
                                f"ADI links this model to a third-party provider ({host}); "
                                "no third-party download was attempted.", product_url)
        text = re.sub(r"\s+", " ", html).lower()
        if any(phrase in text for phrase in _LOGIN_PHRASES):
            return self._result(AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LOGIN_REQUIRED,
                                "ADI indicates that downloading this model requires an authorized login.", product_url)
        if LTSPICE_BUILTIN_MARKER in text:
            return self._result(AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.MODEL_NOT_PUBLISHED,
                                "The model ships inside the LTspice standard library; "
                                "ADI publishes no separate downloadable file.", product_url)
        if MEDIA_PATH_MARKER in text:
            return self._result(AcquisitionStatus.UNAVAILABLE, part, asset,
                                ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE,
                                "ADI lists simulation resources but not a public model in the requested format.", product_url)
        return self._result(AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.MODEL_NOT_PUBLISHED,
                            "ADI's product page does not list a public model in the requested format.", product_url)

    def _http_error(self, part: str, asset: str, product_url: str, status: int, download_url: str | None = None) -> AcquisitionResult:
        if status == 401:
            return self._result(AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LOGIN_REQUIRED,
                                "ADI requires an authorized login for this resource.", product_url, download_url)
        if status == 402:
            return self._result(AcquisitionStatus.PAID, part, asset, ReasonCode.PAID_RESOURCE,
                                "The requested model resource requires payment.", product_url, download_url)
        if status == 403:
            return self._result(AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LICENSE_ACCEPTANCE_REQUIRED,
                                "ADI denied the download (HTTP 403); it may require license acceptance, "
                                "or the ADI edge may be blocking this client.", product_url, download_url)
        if status == 404:
            return self._result(AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                                "ADI did not return the requested resource.", product_url, download_url)
        return self._result(AcquisitionStatus.ERROR, part, asset, ReasonCode.DOWNLOAD_BLOCKED,
                            f"ADI returned HTTP {status} for the requested resource.", product_url, download_url)

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
