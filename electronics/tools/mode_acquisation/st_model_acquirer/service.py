"""ST-first model discovery, download, and validation without Hermes dependencies."""

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
from urllib.parse import unquote, urlencode, urljoin, urlparse, urlunparse

from .status import AcquisitionResult, AcquisitionStatus, ReasonCode


ST_ROOT = "https://www.st.com"
SEARCH_ENDPOINT = f"{ST_ROOT}/bin/st/search/resources"
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100
MODEL_EXTENSIONS = {
    ".lib", ".cir", ".sub", ".mod", ".sp", ".spi", ".mdl", ".txt",
    ".tsm", ".sxsch", ".sxsym", ".sxlib",
}
SUFFIX_TRUSTED = {".tsm", ".sxsch", ".sxsym", ".sxlib"}
SUPPORTED_ASSETS = {"spice", "pspice", "ltspice", "simplis", "simetrix", "eldo"}
RESOURCE_PATHS = {
    "spice": "/resource/en/spice_model/",
    "simplis": "/resource/en/simplis_model/",
    "simetrix": "/resource/en/simetrix_model/",
    "eldo": "/resource/en/eldo_model/",
}

_SNIFF_RE = re.compile(r"(?im)^\s*\.(?:subckt|model)\b")
_ANCHOR_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*(?:\"(?P<dq>[^\"]+)\"|'(?P<sq>[^']+)')[^>]*>"
    r"(?P<text>.*?)</a\s*>", re.I | re.S,
)
_URL_RE = re.compile(r"(?:https?://[^\s\"'<>]+|/resource/en/[A-Za-z0-9_./?=&%+-]+)", re.I)
_LOGIN_PHRASES = ("sign in to download", "login to download", "log in to download", "my.st.com")
_LICENSE_PHRASES = ("license agreement", "licence agreement", "accept the license", "accept the licence")


class LicenseAcceptanceRequired(ValueError):
    pass


class LoginRequired(ValueError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    body: bytes
    content_type: str = ""


@dataclass(frozen=True)
class ModelResource:
    """One model artifact explicitly returned by ST's official resource index."""

    title: str
    url: str


class STModelAcquirer:
    """Download public ST simulation models associated with a part or family."""

    def __init__(self, output_root: Path | str, fetcher=None):
        self.output_root = Path(output_root)
        self.fetcher = fetcher or self._fetch

    def acquire(self, part_number: str, asset_type: str = "spice", *, product_url: str | None = None) -> AcquisitionResult:
        part = part_number.strip().upper()
        asset = asset_type.strip().lower()
        source_url = self._search_url(part)
        if not part:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                "An exact STMicroelectronics part number is required.", source_url,
            )
        if asset not in SUPPORTED_ASSETS:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset,
                ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE,
                f"The standalone acquirer does not support '{asset_type}'.", source_url,
            )

        try:
            index = self.fetcher(source_url)
        except urllib.error.HTTPError as exc:
            return self._http_error(part, asset, source_url, exc.code)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return self._result(
                AcquisitionStatus.ERROR, part, asset, ReasonCode.NETWORK_ERROR,
                f"ST resource lookup failed: {exc}", source_url,
            )
        if index.status == 404:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                "ST did not return resources for this exact part number.", source_url,
            )
        if index.status != 200:
            return self._http_error(part, asset, source_url, index.status)

        candidates, offsite = self._model_resources(
            index.body, index.url, asset, index.content_type,
        )
        if not candidates and not offsite and product_url:
            parsed = urlparse(product_url)
            if (parsed.scheme != "https" or not self._is_st_host(parsed.hostname or "")
                    or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or parsed.query or parsed.fragment
                    or not re.search(rf"(?<![A-Za-z0-9]){re.escape(part)}(?![A-Za-z0-9])", parsed.path, re.I)):
                raise ValueError("product_url must be an official HTTPS ST product page for the exact part")
            try:
                product = self.fetcher(product_url)
            except urllib.error.HTTPError as exc:
                return self._http_error(part, asset, product_url, exc.code)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                return self._result(AcquisitionStatus.ERROR, part, asset, ReasonCode.NETWORK_ERROR,
                                    f"ST product-page lookup failed: {exc}", product_url)
            if product.status != 200:
                return self._http_error(part, asset, product_url, product.status)
            found, offsite = self._model_resources(product.body, product.url, asset, product.content_type)
            candidates = [item for item in found if self._resource_matches_part(item, part)]
            source_url = product_url
        if not candidates:
            return self._no_candidate_result(
                part, asset, source_url, index.body, offsite,
            )

        saved_files: list[Path] = []
        resources: list[dict] = []
        errors: list[dict] = []
        for candidate in candidates:
            try:
                payload = self.fetcher(candidate.url)
                if payload.status != 200:
                    errors.append(self._error_record(candidate, self._http_error(
                        part, asset, source_url, payload.status, candidate.url,
                    )))
                    continue
                files = self._store_download(
                    part, asset, payload.body, candidate.url, payload.content_type,
                )
            except urllib.error.HTTPError as exc:
                errors.append(self._error_record(candidate, self._http_error(
                    part, asset, source_url, exc.code, candidate.url,
                )))
                continue
            except LicenseAcceptanceRequired as exc:
                errors.append({
                    "title": candidate.title,
                    "download_url": candidate.url,
                    "status": AcquisitionStatus.ACCESS_REQUIRED.value,
                    "reason_code": ReasonCode.LICENSE_ACCEPTANCE_REQUIRED.value,
                    "reason": str(exc),
                })
                continue
            except LoginRequired as exc:
                errors.append({
                    "title": candidate.title,
                    "download_url": candidate.url,
                    "status": AcquisitionStatus.ACCESS_REQUIRED.value,
                    "reason_code": ReasonCode.LOGIN_REQUIRED.value,
                    "reason": str(exc),
                })
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, zipfile.BadZipFile) as exc:
                errors.append({
                    "title": candidate.title,
                    "download_url": candidate.url,
                    "status": AcquisitionStatus.ERROR.value,
                    "reason_code": ReasonCode.INVALID_DOWNLOAD.value,
                    "reason": f"ST model file could not be validated: {exc}",
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
                first.get("reason", "ST model files could not be validated."),
                source_url,
                candidates[0].url,
                resource_errors=errors,
            )

        return AcquisitionResult(
            AcquisitionStatus.DOWNLOADED,
            part,
            asset,
            reason=None if not errors else "Some ST model files failed; see resource_errors.",
            source_url=source_url,
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
    def _search_url(part: str) -> str:
        return f"{SEARCH_ENDPOINT}?{urlencode({'q': part, 'limit': 100, 'start': 0})}"

    @staticmethod
    def _is_st_host(host: str) -> bool:
        host = host.lower().rstrip(".")
        return host == "st.com" or host.endswith(".st.com")

    @staticmethod
    def _fetch(url: str) -> HttpResponse:
        requested_host = (urlparse(url).hostname or "").lower()
        if not STModelAcquirer._is_st_host(requested_host):
            raise ValueError("only official st.com URLs are allowed")
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "st-model-acquirer/1.0",
                "Accept": "application/json,text/html,application/zip,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            resolved_host = (urlparse(response.url).hostname or "").lower()
            if not STModelAcquirer._is_st_host(resolved_host):
                raise ValueError("ST download redirected to a non-ST host")
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(body) > MAX_DOWNLOAD_BYTES:
                raise ValueError("response exceeds the 25 MiB limit")
            return HttpResponse(
                response.status,
                response.url,
                body,
                response.headers.get_content_type(),
            )

    def _store_download(
        self,
        part: str,
        asset: str,
        data: bytes,
        source_url: str,
        content_type: str = "",
    ) -> list[Path]:
        """Store the ST artifact byte-identical and safely extract model files."""
        stripped = data.lstrip()[:512].lower()
        if content_type.lower().startswith("text/html") or stripped.startswith(
            (b"<!doctype", b"<html")
        ):
            text = data.decode("utf-8", errors="replace").lower()
            if any(phrase in text for phrase in _LICENSE_PHRASES):
                raise LicenseAcceptanceRequired(
                    "ST requires license acceptance before this model can be downloaded; "
                    "the agreement was not bypassed or accepted automatically."
                )
            if any(phrase in text for phrase in _LOGIN_PHRASES):
                raise LoginRequired(
                    "ST requires an authorized myST login before this model can be downloaded."
                )
            raise ValueError("ST returned an HTML page instead of a model file")

        target = self.output_root / part / asset
        if zipfile.is_zipfile(io.BytesIO(data)):
            return self._store_zip(target, data, source_url)
        return [self._store_raw(target, data, source_url)]

    def _store_zip(self, target: Path, data: bytes, source_url: str) -> list[Path]:
        basename = self._safe_name(Path(unquote(urlparse(source_url).path)).name)
        if not basename:
            raise ValueError("download URL has no usable filename")
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
        if not basename:
            raise ValueError("download URL has no usable filename")
        suffix = Path(basename).suffix.lower()
        if suffix not in MODEL_EXTENSIONS:
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
        cls,
        body: bytes,
        page_url: str,
        asset: str,
        content_type: str = "",
    ) -> tuple[list[ModelResource], list[ModelResource]]:
        text = body.decode("utf-8", errors="replace")
        discovered: list[ModelResource] = []

        if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
            try:
                cls._resources_from_json(json.loads(text), page_url, discovered)
            except json.JSONDecodeError:
                pass

        for match in _ANCHOR_RE.finditer(text):
            url = urljoin(page_url, html_module.unescape(match.group("dq") or match.group("sq")))
            title = re.sub(
                r"\s+", " ",
                re.sub(r"<[^>]+>", " ", html_module.unescape(match.group("text"))),
            ).strip()
            discovered.append(ModelResource(title or cls._title_from_url(url), url))

        # Some ST JSON fields contain HTML snippets or bare resource URLs.
        for match in _URL_RE.finditer(text):
            value = html_module.unescape(match.group(0)).rstrip(".,;)")
            url = urljoin(page_url, value)
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
            if not cls._is_st_host(host):
                offsite.append(item)
            else:
                resources.append(ModelResource(item.title, canonical))
        return resources, offsite

    @classmethod
    def _resources_from_json(
        cls,
        node: object,
        page_url: str,
        output: list[ModelResource],
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
            }:
                title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()
                if title:
                    break
        for value in node.values():
            if isinstance(value, str):
                for match in _URL_RE.finditer(value):
                    url = urljoin(page_url, html_module.unescape(match.group(0)).rstrip(".,;)") )
                    output.append(ModelResource(title or cls._title_from_url(url), url))
            else:
                cls._resources_from_json(value, page_url, output)

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", "", ""))

    @staticmethod
    def _title_from_url(url: str) -> str:
        name = Path(unquote(urlparse(url).path)).name
        return re.sub(r"[_-]+", " ", Path(name).stem).strip()

    @staticmethod
    def _looks_like_model_resource(item: ModelResource, asset: str) -> bool:
        parsed = urlparse(item.url)
        path = parsed.path.lower()
        title = item.title.lower()
        host = (parsed.hostname or "").lower()
        is_spice_path = RESOURCE_PATHS["spice"] in path

        # Keep explicit off-site simulation-model cards visible long enough for
        # the caller to report THIRD_PARTY_PROVIDER_REQUIRED.  They are never
        # fetched.  Official ST resources use the typed paths handled below.
        if not STModelAcquirer._is_st_host(host):
            suffix = Path(path).suffix.lower()
            downloadable = suffix == ".zip" or suffix in MODEL_EXTENSIONS
            if not downloadable:
                return False
            if asset == "simplis":
                return "simplis" in title
            if asset == "simetrix":
                return "simetrix" in title
            if asset == "eldo":
                return "eldo" in title
            if asset == "ltspice":
                return "ltspice" in title
            if asset == "pspice":
                return "pspice" in title or "pspice" in path
            return "spice" in title or "spice" in path

        if asset == "simplis":
            return RESOURCE_PATHS["simplis"] in path
        if asset == "simetrix":
            return RESOURCE_PATHS["simetrix"] in path
        if asset == "eldo":
            return RESOURCE_PATHS["eldo"] in path
        if not is_spice_path:
            return False
        if any(term in title for term in ("ibis", "tech report", "tech-report", "datasheet")):
            return False
        if Path(path).suffix.lower() == ".pdf":
            return False
        if asset == "ltspice":
            return "ltspice" in title or "ltspice" in path
        if asset == "pspice":
            return "ltspice" not in title and "ltspice" not in path
        return True

    @staticmethod
    def _resource_matches_part(item: ModelResource, part: str) -> bool:
        haystack = f"{item.title} {unquote(urlparse(item.url).path)}".upper()
        return re.search(rf"(?<![A-Z0-9]){re.escape(part)}(?![A-Z0-9])", haystack) is not None

    def _no_candidate_result(
        self,
        part: str,
        asset: str,
        source_url: str,
        body: bytes,
        offsite: list[ModelResource],
    ) -> AcquisitionResult:
        if offsite:
            host = urlparse(offsite[0].url).hostname or "a third-party site"
            return self._result(
                AcquisitionStatus.ACCESS_REQUIRED, part, asset,
                ReasonCode.THIRD_PARTY_PROVIDER_REQUIRED,
                f"ST links this model to a third-party provider ({host}); "
                "no third-party download was attempted.", source_url,
            )
        text = re.sub(r"\s+", " ", body.decode("utf-8", errors="replace")).lower()
        if any(phrase in text for phrase in _LOGIN_PHRASES):
            return self._result(
                AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LOGIN_REQUIRED,
                "ST indicates that downloading this model requires an authorized myST login.",
                source_url,
            )
        requested_marker = RESOURCE_PATHS["spice"] if asset in {"spice", "pspice", "ltspice"} else RESOURCE_PATHS[asset]
        if any(marker in text for marker in RESOURCE_PATHS.values()) and requested_marker not in text:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset,
                ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE,
                "ST lists simulation resources but not a public model in the requested format.",
                source_url,
            )
        return self._result(
            AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.MODEL_NOT_PUBLISHED,
            "ST's official resource index does not list a public model in the requested format.",
            source_url,
        )

    def _http_error(
        self,
        part: str,
        asset: str,
        source_url: str,
        status_code: int,
        download_url: str | None = None,
    ) -> AcquisitionResult:
        if status_code == 401:
            return self._result(
                AcquisitionStatus.ACCESS_REQUIRED, part, asset, ReasonCode.LOGIN_REQUIRED,
                "ST requires an authorized myST login for this resource.", source_url, download_url,
            )
        if status_code == 402:
            return self._result(
                AcquisitionStatus.PAID, part, asset, ReasonCode.PAID_RESOURCE,
                "The requested model resource requires payment.", source_url, download_url,
            )
        if status_code == 403:
            return self._result(
                AcquisitionStatus.ACCESS_REQUIRED, part, asset,
                ReasonCode.LICENSE_ACCEPTANCE_REQUIRED,
                "ST denied the download; it may require license acceptance or authorized access.",
                source_url, download_url,
            )
        if status_code == 404:
            return self._result(
                AcquisitionStatus.UNAVAILABLE, part, asset, ReasonCode.PART_NOT_FOUND,
                "ST did not return the requested resource.", source_url, download_url,
            )
        return self._result(
            AcquisitionStatus.ERROR, part, asset, ReasonCode.DOWNLOAD_BLOCKED,
            f"ST returned HTTP {status_code} for the requested resource.",
            source_url, download_url,
        )

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
