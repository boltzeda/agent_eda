"""NXP-first model discovery, download, and validation without Hermes dependencies."""

from __future__ import annotations

import hashlib
import html as html_module
import io
import json
import re
import shutil
import stat
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

from .status import AcquisitionResult, AcquisitionStatus, ReasonCode


NXP_ROOT = "https://www.nxp.com"
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100
MODEL_EXTENSIONS = {
    ".lib", ".cir", ".sub", ".mod", ".prm", ".sp", ".spi", ".mdl",
    ".txt", ".tsm", ".sxsch", ".sxsym", ".sxlib",
}
SUFFIX_TRUSTED = {".tsm", ".sxsch", ".sxsym", ".sxlib"}
SUPPORTED_ASSETS = {"spice", "pspice", "simplis"}

_SNIFF_RE = re.compile(r"(?im)^\s*\.(?:subckt|model)\b")
_ANCHOR_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*?)\bhref\s*=\s*(?:\"(?P<dq>[^\"]+)\"|'(?P<sq>[^']+)')"
    r"(?P<tail>[^>]*)>(?P<text>.*?)</a\s*>", re.I | re.S,
)
_HEADING_RE = re.compile(r"<h[1-6]\b[^>]*>(?P<text>.*?)</h[1-6]\s*>", re.I | re.S)
_TITLE_ATTR_RE = re.compile(
    r"(?:data-title|aria-label|title)\s*=\s*(?:\"(?P<dq>[^\"]+)\"|'(?P<sq>[^']+)')",
    re.I,
)
_URL_RE = re.compile(
    r"(?:https?://[^\s\"'<>]+|/(?:downloads/en/|webapp/(?:Download|sps/download/))[^\s\"'<>]+)",
    re.I,
)
_LOGIN_PHRASES = (
    "account required", "sign in to download", "login to download",
    "log in to download", "sign in", "login.nxp.com",
)
_LICENSE_PHRASES = (
    "license agreement", "licence agreement", "accept the license",
    "accept the licence", "software terms and conditions",
)
_BLOCKED_PHRASES = (
    "sorry! this page is not available", "contact your administrator with the error code",
    "access denied", "request rejected",
)


class LicenseAcceptanceRequired(ValueError):
    pass


class LoginRequired(ValueError):
    pass


class DownloadBlocked(ValueError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    body: bytes
    content_type: str = ""


@dataclass(frozen=True)
class ModelResource:
    """One simulation-model resource explicitly listed by NXP."""

    title: str
    url: str


class _NXPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from following an NXP link to an untrusted host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = (urlparse(newurl).hostname or "").lower()
        if not NXPModelAcquirer._is_nxp_host(host):
            raise ValueError("NXP redirected the request to a non-NXP host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NXPModelAcquirer:
    """Download public NXP SPICE-family models for an exact part or family."""

    def __init__(self, output_root: Path | str, fetcher=None):
        self.output_root = Path(output_root)
        self.fetcher = fetcher or self._fetch

    def acquire(self, part_number: str, asset_type: str = "spice") -> AcquisitionResult:
        part = part_number.strip().upper()
        asset = asset_type.strip().lower()
        product_url = self._product_url(part)
        if not part:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                "An exact NXP part number is required.", product_url,
            )
        if asset not in SUPPORTED_ASSETS:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset,
                ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE,
                f"The standalone acquirer does not support '{asset_type}'.", product_url,
            )

        try:
            page = self.fetcher(product_url)
        except urllib.error.HTTPError as exc:
            return self._http_error(part, asset, product_url, exc.code)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return self._result(
                AcquisitionStatus.ERROR, part, asset, ReasonCode.NETWORK_ERROR,
                f"NXP product-page lookup failed: {exc}", product_url,
            )
        page_text = page.body.decode("utf-8", errors="replace")
        lowered = page_text.lower()
        if any(phrase in lowered for phrase in _BLOCKED_PHRASES):
            return self._result(
                AcquisitionStatus.ERROR, part, asset, ReasonCode.DOWNLOAD_BLOCKED,
                "NXP's edge service blocked or could not serve the product page.", product_url,
            )
        if page.status == 404:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                "NXP did not return a product page for this exact part number.", product_url,
            )
        if page.status != 200:
            return self._http_error(part, asset, product_url, page.status)

        if "page not found" in lowered and part.lower() not in lowered:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                "NXP did not return a product page for this exact part number.", product_url,
            )

        candidates, offsite = self._model_resources(page_text, page.url, asset)
        if not candidates:
            return self._no_candidate_result(
                part, asset, product_url, page_text, offsite,
            )

        saved_files: list[Path] = []
        resources: list[dict] = []
        errors: list[dict] = []
        for candidate in candidates:
            try:
                payload = self.fetcher(candidate.url)
                if payload.status != 200:
                    if self._has_block_page(payload.body):
                        errors.append({
                            "title": candidate.title,
                            "download_url": candidate.url,
                            "status": AcquisitionStatus.ERROR.value,
                            "reason_code": ReasonCode.DOWNLOAD_BLOCKED.value,
                            "reason": "NXP's edge service blocked the model download.",
                        })
                        continue
                    errors.append(self._error_record(candidate, self._http_error(
                        part, asset, product_url, payload.status, candidate.url,
                    )))
                    continue
                files = self._store_download(
                    part, asset, payload.body, candidate.url, payload.content_type,
                )
            except urllib.error.HTTPError as exc:
                errors.append(self._error_record(candidate, self._http_error(
                    part, asset, product_url, exc.code, candidate.url,
                )))
                continue
            except LicenseAcceptanceRequired as exc:
                errors.append(self._access_error(
                    candidate, ReasonCode.LICENSE_ACCEPTANCE_REQUIRED, str(exc),
                ))
                continue
            except LoginRequired as exc:
                errors.append(self._access_error(
                    candidate, ReasonCode.LOGIN_REQUIRED, str(exc),
                ))
                continue
            except DownloadBlocked as exc:
                errors.append({
                    "title": candidate.title,
                    "download_url": candidate.url,
                    "status": AcquisitionStatus.ERROR.value,
                    "reason_code": ReasonCode.DOWNLOAD_BLOCKED.value,
                    "reason": str(exc),
                })
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, zipfile.BadZipFile) as exc:
                errors.append({
                    "title": candidate.title,
                    "download_url": candidate.url,
                    "status": AcquisitionStatus.ERROR.value,
                    "reason_code": ReasonCode.INVALID_DOWNLOAD.value,
                    "reason": f"NXP model file could not be validated: {exc}",
                })
                continue
            saved_files.extend(files)
            resources.append({
                "title": candidate.title,
                "download_url": candidate.url,
                "files": [str(path) for path in files],
            })

        if not saved_files:
            first = errors[0] if errors else {}
            return AcquisitionResult(
                AcquisitionStatus(first.get("status", AcquisitionStatus.ERROR)),
                part,
                asset,
                ReasonCode(first.get("reason_code", ReasonCode.INVALID_DOWNLOAD)),
                first.get("reason", "NXP model files could not be validated."),
                product_url,
                candidates[0].url,
                resource_errors=errors,
            )

        return AcquisitionResult(
            AcquisitionStatus.DOWNLOADED,
            part,
            asset,
            reason=None if not errors else "Some NXP model files failed; see resource_errors.",
            source_url=product_url,
            download_url=candidates[0].url,
            files=[str(path) for path in saved_files],
            sha256={str(path): self._sha256(path) for path in saved_files},
            model_resources=resources,
            exact_part_model_found=any(
                self._resource_matches_part(item, part) for item in candidates
            ),
            series_model_count=len(resources),
            resource_errors=errors,
        )

    @staticmethod
    def _product_url(part: str) -> str:
        return f"{NXP_ROOT}/products/{quote(part, safe='-_')}"

    @staticmethod
    def _is_nxp_host(host: str) -> bool:
        host = host.lower().rstrip(".")
        return host == "nxp.com" or host.endswith(".nxp.com")

    @staticmethod
    def _fetch(url: str) -> HttpResponse:
        requested_host = (urlparse(url).hostname or "").lower()
        if not NXPModelAcquirer._is_nxp_host(requested_host):
            raise ValueError("only official nxp.com URLs are allowed")
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/zip,application/octet-stream,*/*;q=0.8",
            },
        )
        opener = urllib.request.build_opener(_NXPRedirectHandler())
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_DOWNLOAD_BYTES + 1)
            if len(body) > MAX_DOWNLOAD_BYTES:
                raise ValueError("response exceeds the 25 MiB limit")
            return HttpResponse(
                exc.code,
                exc.url,
                body,
                exc.headers.get_content_type() if exc.headers else "",
            )
        with response:
            resolved_host = (urlparse(response.url).hostname or "").lower()
            if not NXPModelAcquirer._is_nxp_host(resolved_host):
                raise ValueError("NXP download redirected to a non-NXP host")
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(body) > MAX_DOWNLOAD_BYTES:
                raise ValueError("response exceeds the 25 MiB limit")
            return HttpResponse(
                response.status,
                response.url,
                body,
                response.headers.get_content_type(),
            )

    @staticmethod
    def _has_block_page(body: bytes) -> bool:
        text = body.decode("utf-8", errors="replace").lower()
        return any(phrase in text for phrase in _BLOCKED_PHRASES)

    def _store_download(
        self,
        part: str,
        asset: str,
        data: bytes,
        source_url: str,
        content_type: str = "",
    ) -> list[Path]:
        stripped = data.lstrip()[:512].lower()
        if content_type.lower().startswith("text/html") or stripped.startswith(
            (b"<!doctype", b"<html")
        ):
            text = data.decode("utf-8", errors="replace").lower()
            if any(phrase in text for phrase in _LICENSE_PHRASES):
                raise LicenseAcceptanceRequired(
                    "NXP requires license acceptance before this model can be downloaded; "
                    "the agreement was not accepted automatically."
                )
            if any(phrase in text for phrase in _LOGIN_PHRASES):
                raise LoginRequired(
                    "NXP requires an authorized account before this model can be downloaded."
                )
            if any(phrase in text for phrase in _BLOCKED_PHRASES):
                raise DownloadBlocked("NXP's edge service blocked the model download.")
            raise ValueError("NXP returned an HTML page instead of a model file")

        target = self.output_root / part / asset
        if zipfile.is_zipfile(io.BytesIO(data)):
            return self._store_zip(target, data, source_url)
        return [self._store_raw(target, data, source_url)]

    def _store_zip(self, target: Path, data: bytes, source_url: str) -> list[Path]:
        basename = self._safe_name(Path(unquote(urlparse(source_url).path)).name)
        if not basename or basename.lower() == "download":
            basename = self._safe_name(self._query_filename(source_url)) or "nxp-model.zip"
        if not basename.lower().endswith(".zip"):
            basename += ".zip"
        archive_id = Path(basename).stem

        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            infos = bundle.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("archive contains too many members")
            total_size = 0
            for info in infos:
                member = Path(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if member.is_absolute() or ".." in member.parts or mode == stat.S_IFLNK:
                    raise ValueError("archive contains an unsafe path or symbolic link")
                if info.file_size > MAX_DOWNLOAD_BYTES:
                    raise ValueError("archive member exceeds the size limit")
                total_size += info.file_size
                if total_size > MAX_EXTRACTED_BYTES:
                    raise ValueError("archive expands beyond the 50 MiB limit")

            target.mkdir(parents=True, exist_ok=True)
            archive = target / basename
            archive.write_bytes(data)
            extract_root = target / "extracted" / archive_id
            if extract_root.exists():
                shutil.rmtree(extract_root)
            extract_root.mkdir(parents=True)
            for info in infos:
                member = Path(info.filename)
                if info.is_dir() or member.suffix.lower() not in MODEL_EXTENSIONS:
                    continue
                destination = extract_root / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)

        files = sorted(
            path for path in extract_root.rglob("*")
            if path.is_file() and self._is_model_file(path)
        )
        if not files:
            shutil.rmtree(extract_root)
            raise ValueError("archive contains no recognized simulation-model file")
        return files

    def _store_raw(self, target: Path, data: bytes, source_url: str) -> Path:
        basename = self._safe_name(Path(unquote(urlparse(source_url).path)).name)
        suffix = Path(basename).suffix.lower()
        if not basename or suffix not in MODEL_EXTENSIONS:
            raise ValueError(f"'{suffix}' is not a recognized simulation-model extension")
        if suffix not in SUFFIX_TRUSTED:
            text = data.decode("utf-8", errors="replace")[:2 * 1024 * 1024]
            if not _SNIFF_RE.search(text):
                raise ValueError("file does not contain a .SUBCKT/.MODEL definition")
        target.mkdir(parents=True, exist_ok=True)
        destination = target / basename
        if destination.exists() and self._sha256(destination) != hashlib.sha256(data).hexdigest():
            destination = target / (
                f"{Path(basename).stem}-"
                f"{hashlib.sha256(source_url.encode()).hexdigest()[:8]}{suffix}"
            )
        destination.write_bytes(data)
        return destination

    @staticmethod
    def _query_filename(url: str) -> str:
        query = urlparse(url).query
        match = re.search(r"(?:^|&)(?:colCode|file)=([^&]+)", query, re.I)
        return unquote(match.group(1)) if match else ""

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")

    @staticmethod
    def _is_model_file(path: Path) -> bool:
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

    @classmethod
    def _model_resources(
        cls, page_text: str, page_url: str, asset: str,
    ) -> tuple[list[ModelResource], list[ModelResource]]:
        discovered: list[ModelResource] = []

        if page_text.lstrip().startswith(("{", "[")):
            try:
                cls._resources_from_json(json.loads(page_text), page_url, discovered)
            except json.JSONDecodeError:
                pass

        for match in _ANCHOR_RE.finditer(page_text):
            raw_url = html_module.unescape(match.group("dq") or match.group("sq"))
            url = urljoin(page_url, raw_url)
            title = cls._anchor_title(match, page_text)
            discovered.append(ModelResource(title or cls._title_from_url(url), url))

        for match in _URL_RE.finditer(page_text):
            raw_url = html_module.unescape(match.group(0)).rstrip(".,;)")
            url = urljoin(page_url, raw_url)
            discovered.append(ModelResource(cls._title_from_url(url), url))

        resources: list[ModelResource] = []
        offsite: list[ModelResource] = []
        seen: set[str] = set()
        for item in discovered:
            canonical = cls._canonical_url(item.url)
            if canonical in seen or not cls._looks_like_model_resource(item, asset):
                continue
            seen.add(canonical)
            host = (urlparse(item.url).hostname or "").lower()
            if cls._is_nxp_host(host):
                resources.append(ModelResource(item.title, canonical))
            else:
                offsite.append(item)
        return resources, offsite

    @classmethod
    def _anchor_title(cls, match: re.Match, page_text: str) -> str:
        attrs = f"{match.group('attrs')} {match.group('tail')}"
        attr = _TITLE_ATTR_RE.search(attrs)
        if attr:
            value = attr.group("dq") or attr.group("sq")
            if value and value.lower() not in {"download", "download file"}:
                return cls._clean_text(value)
        anchor_text = cls._clean_text(match.group("text"))
        if anchor_text and anchor_text.lower() not in {
            "download", "downloadtoggle dropdown", "add to queue", "view queue",
        }:
            return anchor_text
        prior = page_text[max(0, match.start() - 3000):match.start()]
        headings = list(_HEADING_RE.finditer(prior))
        return cls._clean_text(headings[-1].group("text")) if headings else ""

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(
            r"\s+", " ",
            re.sub(r"<[^>]+>", " ", html_module.unescape(value)),
        ).strip()

    @classmethod
    def _resources_from_json(
        cls, node: object, page_url: str, output: list[ModelResource],
    ) -> None:
        if isinstance(node, list):
            for item in node:
                cls._resources_from_json(item, page_url, output)
            return
        if not isinstance(node, dict):
            return
        title = ""
        for key, value in node.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if isinstance(value, str) and normalized in {
                "title", "name", "label", "documenttitle", "resourcetitle",
                "displayname", "description",
            }:
                title = cls._clean_text(value)
                if title:
                    break
        for value in node.values():
            if isinstance(value, str):
                for match in _URL_RE.finditer(value):
                    raw_url = html_module.unescape(match.group(0)).rstrip(".,;)")
                    url = urljoin(page_url, raw_url)
                    output.append(ModelResource(title or cls._title_from_url(url), url))
            else:
                cls._resources_from_json(value, page_url, output)

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        query = parsed.query if "/webapp/" in parsed.path.lower() else ""
        return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", query, ""))

    @staticmethod
    def _title_from_url(url: str) -> str:
        name = Path(unquote(urlparse(url).path)).name
        if name.lower() == "download":
            name = NXPModelAcquirer._query_filename(url)
        return re.sub(r"[_-]+", " ", Path(name).stem).strip()

    @staticmethod
    def _looks_like_model_resource(item: ModelResource, asset: str) -> bool:
        parsed = urlparse(item.url)
        path = parsed.path.lower()
        title = item.title.lower()
        suffix = Path(path).suffix.lower()
        host = (parsed.hostname or "").lower()
        model_word = "simplis" if asset == "simplis" else "spice"

        if not NXPModelAcquirer._is_nxp_host(host):
            return model_word in title and (suffix == ".zip" or suffix in MODEL_EXTENSIONS)
        if suffix in {".s1p", ".s2p", ".s3p", ".s4p", ".pdf"}:
            return False
        if asset == "simplis":
            return "simplis" in title and (
                "/downloads/" in path or "/webapp/" in path
            )
        if "/downloads/en/spice-model/" in path:
            return suffix == ".zip" or suffix in MODEL_EXTENSIONS
        return "spice" in title and (
            "/downloads/" in path or "/webapp/" in path
        )

    @staticmethod
    def _resource_matches_part(item: ModelResource, part: str) -> bool:
        haystack = f"{item.title} {unquote(item.url)}".upper()
        return re.search(rf"(?<![A-Z0-9]){re.escape(part)}(?![A-Z0-9])", haystack) is not None

    def _no_candidate_result(
        self,
        part: str,
        asset: str,
        product_url: str,
        page_text: str,
        offsite: list[ModelResource],
    ) -> AcquisitionResult:
        if offsite:
            host = urlparse(offsite[0].url).hostname or "a third-party site"
            return self._result(
                AcquisitionStatus.ACCESS_REQUIRED, part, asset,
                ReasonCode.THIRD_PARTY_PROVIDER_REQUIRED,
                f"NXP links this model to a third-party provider ({host}); "
                "no third-party download was attempted.", product_url,
            )
        text = re.sub(r"\s+", " ", page_text).lower()
        requested_word = "simplis" if asset == "simplis" else "spice"
        if requested_word in text and any(phrase in text for phrase in _LOGIN_PHRASES):
            return self._result(
                AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LOGIN_REQUIRED,
                "NXP lists the requested model but requires an authorized account.",
                product_url,
            )
        other_word = "spice" if asset == "simplis" else "simplis"
        if other_word in text and requested_word not in text:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset,
                ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE,
                "NXP lists simulation resources but not a public model in the requested format.",
                product_url,
            )
        return self._result(
            AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.MODEL_NOT_PUBLISHED,
            "NXP's product page does not list a public model in the requested format.",
            product_url,
        )

    def _http_error(
        self,
        part: str,
        asset: str,
        product_url: str,
        status_code: int,
        download_url: str | None = None,
    ) -> AcquisitionResult:
        if status_code == 401:
            return self._result(
                AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LOGIN_REQUIRED,
                "NXP requires an authorized account for this resource.", product_url, download_url,
            )
        if status_code == 402:
            return self._result(
                AcquisitionStatus.PAID, part, asset, ReasonCode.PAID_RESOURCE,
                "The requested model resource requires payment.", product_url, download_url,
            )
        if status_code == 403:
            return self._result(
                AcquisitionStatus.ACCESS_REQUIRED, part, asset,
                ReasonCode.LICENSE_ACCEPTANCE_REQUIRED,
                "NXP denied the download; it may require login or license acceptance.",
                product_url, download_url,
            )
        if status_code == 404:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                "NXP did not return the requested resource.", product_url, download_url,
            )
        return self._result(
            AcquisitionStatus.ERROR, part, asset, ReasonCode.DOWNLOAD_BLOCKED,
            f"NXP returned HTTP {status_code} for the requested resource.",
            product_url, download_url,
        )

    @staticmethod
    def _access_error(candidate: ModelResource, code: ReasonCode, reason: str) -> dict:
        return {
            "title": candidate.title,
            "download_url": candidate.url,
            "status": AcquisitionStatus.ACCESS_REQUIRED.value,
            "reason_code": code.value,
            "reason": reason,
        }

    @staticmethod
    def _error_record(candidate: ModelResource, result: AcquisitionResult) -> dict:
        return {
            "title": candidate.title,
            "download_url": candidate.url,
            "status": result.status.value,
            "reason_code": result.reason_code.value if result.reason_code else None,
            "reason": result.reason,
        }

    @staticmethod
    def _result(status, part, asset, code, reason, source_url, download_url=None) -> AcquisitionResult:
        return AcquisitionResult(status, part, asset, code, reason, source_url, download_url)

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
