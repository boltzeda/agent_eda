"""Download an ADI SPICE model by clicking its license button with opt-in.

Uses the project's existing agent-browser CLI in an isolated browser session.
It never attaches to a personal Brave profile or invents a license-bypass URL.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from urllib.parse import parse_qsl, urlencode, urlparse
from uuid import uuid4

from .service import ADI_HOSTS, ADIModelAcquirer, MAX_DOWNLOAD_BYTES, MEDIA_PATH_MARKER
from .status import AcquisitionResult, AcquisitionStatus, ReasonCode


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUTTON_NAME = "Accept and Download"
BROWSER_ALLOWED_DOMAINS = "analog.com,www.analog.com,assets2.analog.com"


class BrowserDownloadError(RuntimeError):
    pass


def license_url_for(model_url: str) -> str:
    parsed = urlparse(model_url)
    if (parsed.scheme != "https" or (parsed.hostname or "").lower() not in ADI_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not parsed.path.lower().startswith(MEDIA_PATH_MARKER + "spice-models/")
            or parsed.query or parsed.fragment):
        raise ValueError("model_url must be an official HTTPS analog.com /media/en/simulation-models/spice-models/ URL")
    if Path(parsed.path).suffix.lower() not in {".cir", ".lib", ".sub", ".mod", ".sp", ".spi", ".mdl", ".txt", ".zip"}:
        raise ValueError("model_url must identify a SPICE model or ZIP archive")
    return "https://www.analog.com/en/license/spice-models.html?" + urlencode({
        "mediaPath": parsed.path, "modelType": "spice-models",
    })


class AgentBrowser:
    """Small adapter around an isolated, bounded agent-browser session."""

    def __init__(self, download_dir: Path, timeout_seconds: float,
                 browser_executable: str | None = None, headed: bool = False,
                 *, allowed_domains: str = BROWSER_ALLOWED_DOMAINS):
        bundled = PROJECT_ROOT / ".hermes" / "node" / "bin" / "agent-browser"
        executable = os.getenv("ADI_AGENT_BROWSER_BIN") or (str(bundled) if bundled.is_file() else shutil.which("agent-browser"))
        if not executable:
            raise BrowserDownloadError("agent-browser was not found; use the project's bundled CLI or set ADI_AGENT_BROWSER_BIN")
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_BROWSER_")}
        self.env["PATH"] = str(bundled.parent) + os.pathsep + self.env.get("PATH", "")
        self.env["AGENT_BROWSER_DEFAULT_TIMEOUT"] = str(int(timeout_seconds * 1000))
        self.prefix = [executable, "--session", "adi-download-" + uuid4().hex,
                       "--json", "--download-path", str(download_dir),
                       "--allowed-domains", allowed_domains]
        browser_executable = browser_executable or next((path for name in (
            "brave-browser", "brave", "google-chrome", "chromium", "chromium-browser",
        ) if (path := shutil.which(name))), None)
        if browser_executable:
            self.prefix += ["--executable-path", browser_executable]
        if headed:
            self.prefix.append("--headed")
        self.deadline = time.monotonic() + timeout_seconds

    def run(self, *args: str) -> dict:
        stage = args[0] if args else "unknown"
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BrowserDownloadError(f"ADI browser step '{stage}' exceeded the total timeout")
        try:
            completed = subprocess.run(self.prefix + list(args), env=self.env,
                                       capture_output=True, text=True, timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise BrowserDownloadError(f"ADI browser step '{stage}' exceeded the remaining timeout ({remaining:.1f}s)") from exc
        try:
            payload = json.loads(completed.stdout)
        except ValueError as exc:
            raise BrowserDownloadError(f"ADI browser step '{stage}' did not return JSON: " + completed.stderr[-500:]) from exc
        if completed.returncode or not payload.get("success"):
            raise BrowserDownloadError(f"ADI browser step '{stage}' failed: " + str(payload.get("error") or completed.stderr[-500:] or "browser command failed"))
        return payload.get("data") or {}

    def close(self) -> None:
        try:
            subprocess.run(self.prefix + ["close"], env=self.env,
                           capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def click_license_download(browser, license_url: str, destination: Path) -> dict:
    """Perform the real UI click; kept separate for local browser fixtures."""
    browser.run("open", license_url)
    current = browser.run("get", "url").get("url", "")
    requested, visited = urlparse(license_url), urlparse(current)
    if (visited.scheme, visited.hostname, visited.path, sorted(parse_qsl(visited.query))) != (
            requested.scheme, requested.hostname, requested.path, sorted(parse_qsl(requested.query))):
        raise BrowserDownloadError("Browser left the expected ADI license page; no consent button was clicked")
    snapshot = browser.run("snapshot", "-i")
    matches = [ref for ref, item in snapshot.get("refs", {}).items()
               if item.get("role") == "button"
               and " ".join(item.get("name", "").split()).casefold() == BUTTON_NAME.casefold()]
    if len(matches) != 1:
        raise BrowserDownloadError("A unique Accept and Download button was not found; review the page in a browser")
    # The native browser's download click does not bring an offscreen target
    # into view. ADI's license text places this button below a small viewport.
    browser.run("scrollintoview", "@" + matches[0])
    clicked_at = datetime.now(timezone.utc).isoformat()
    download = browser.run("download", "@" + matches[0], str(destination))
    if Path(str(download.get("path") or "")).resolve() != destination.resolve() or not destination.is_file():
        raise BrowserDownloadError("Browser did not save the expected model file")
    return {"authorized": True, "button": BUTTON_NAME, "license_page": current,
            "clicked_at_utc": clicked_at}


def download_model(part_number: str, model_url: str, output_root: Path | str,
                   *, accept_license: bool = False, timeout_seconds: float | None = None,
                   browser_executable: str | None = None, headed: bool = False) -> AcquisitionResult:
    part = part_number.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,127}", part):
        raise ValueError("part_number must be an exact ADI part number")
    gate = license_url_for(model_url)
    root = Path(output_root).expanduser().resolve()
    timeout_seconds = float(timeout_seconds if timeout_seconds is not None
                            else os.getenv("ADI_BROWSER_TIMEOUT_SECONDS", "180"))
    acquirer = ADIModelAcquirer(root, timeout_seconds=timeout_seconds)
    if not accept_license:
        return AcquisitionResult(
            AcquisitionStatus.ACCESS_REQUIRED, part, "spice", ReasonCode.LICENSE_ACCEPTANCE_REQUIRED,
            "Review the ADI license and authorize its button click with --accept-license; no browser was started.",
            gate, model_url, acquisition_method="browser_click",
        )
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".adi-browser-", dir=root) as temporary:
            directory = Path(temporary)
            destination = directory / Path(urlparse(model_url).path).name
            browser = AgentBrowser(directory, timeout_seconds, browser_executable, headed)
            try:
                consent = click_license_download(browser, gate, destination)
            finally:
                browser.close()
            if destination.stat().st_size > MAX_DOWNLOAD_BYTES:
                raise BrowserDownloadError("Downloaded model exceeds the 25 MiB limit")
            files = acquirer._store_download(part, "spice", destination.read_bytes(), model_url)
            # Reuse the plugin's structural validation and record declared-part coverage.
            names = []
            for file in files:
                names.extend(re.findall(r"(?im)^\s*\.(?:subckt|model)\s+(\S+)", file.read_text(errors="replace")))
            exact = any(acquirer._title_matches_part(name, part) for name in names)
            if not exact:
                raise BrowserDownloadError("Downloaded SPICE declarations do not match the requested part")
        return AcquisitionResult(
            AcquisitionStatus.DOWNLOADED, part, "spice", source_url=gate, download_url=model_url,
            files=[str(file.resolve()) for file in files],
            sha256={str(file.resolve()): acquirer._sha256(file) for file in files},
            model_resources=[{"title": f"{part} SPICE model", "download_url": model_url,
                              "files": [str(file.resolve()) for file in files],
                              "license_acceptance": consent}],
            exact_part_model_found=exact, series_model_count=1, acquisition_method="browser_click",
        )
    except (BrowserDownloadError, OSError, ValueError, zipfile.BadZipFile) as exc:
        return AcquisitionResult(AcquisitionStatus.ERROR, part, "spice", ReasonCode.DOWNLOAD_BLOCKED,
                                 str(exc), gate, model_url, acquisition_method="browser_click")


def download_from_product_page(part_number: str, product_url: str, output_root: Path | str,
                               *, asset_type: str = "spice", timeout_seconds: float | None = None,
                               browser_executable: str | None = None,
                               headed: bool = False) -> AcquisitionResult:
    """Resolve an exact-part SPICE card in the official page, then accept and download it."""
    part = part_number.strip().upper()
    expected = f"https://www.analog.com/en/products/{part.lower()}.html"
    parsed = urlparse(product_url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in ADI_HOSTS
        or parsed.path.lower() != urlparse(expected).path
        or parsed.query
        or parsed.fragment
    ):
        return AcquisitionResult(
            AcquisitionStatus.ERROR, part, asset_type, ReasonCode.INVALID_DOWNLOAD,
            "Product-page discovery requires the exact official ADI product URL.",
            expected, product_url, acquisition_method="browser_click",
        )
    if asset_type not in {"spice", "pspice"}:
        return AcquisitionResult(
            AcquisitionStatus.UNAVAILABLE, part, asset_type,
            ReasonCode.REQUESTED_FORMAT_NOT_AVAILABLE,
            "Browser product-page discovery currently supports SPICE models only.",
            expected, acquisition_method="browser_click",
        )

    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    timeout = float(timeout_seconds if timeout_seconds is not None
                    else os.getenv("ADI_BROWSER_TIMEOUT_SECONDS", "180"))
    acquirer = ADIModelAcquirer(root, timeout_seconds=timeout)
    try:
        with tempfile.TemporaryDirectory(prefix=".adi-discovery-", dir=root) as temporary:
            browser = AgentBrowser(Path(temporary), timeout, browser_executable, headed)
            try:
                browser.run("open", expected)
                current_url = str(browser.run("get", "url").get("url") or "")
                current = urlparse(current_url)
                if ((current.hostname or "").lower() not in ADI_HOSTS
                        or current.path.lower() != urlparse(expected).path):
                    raise BrowserDownloadError(
                        "ADI product-page discovery left the exact official product page"
                    )
                body = browser.run("get", "html", "body")
                if isinstance(body, dict):
                    body = next((body.get(key) for key in ("html", "text", "content", "value")
                                 if body.get(key)), "")
                html = str(body or "")
                if not html:
                    raise BrowserDownloadError("ADI product page returned no readable page content")
                if any(marker in html.casefold() for marker in (
                    "access denied", "errors.edgesuite.net", "reference #",
                )):
                    raise BrowserDownloadError(
                        "ADI denied access to the product page; model discovery must use another official route"
                    )
                candidates, offsite = acquirer._model_resources(html, current_url, "spice")
                exact = [item for item in candidates
                         if acquirer._title_matches_part(item.title, part)]
            finally:
                browser.close()
    except (BrowserDownloadError, OSError, ValueError) as exc:
        return AcquisitionResult(
            AcquisitionStatus.ERROR, part, "spice", ReasonCode.NETWORK_ERROR,
            f"ADI browser product-page lookup failed: {exc}", expected,
            acquisition_method="browser_click",
        )

    if not exact:
        result = acquirer._no_candidate_result(part, "spice", expected, html, offsite)
        result.acquisition_method = "browser_click"
        return result
    # Only an exact-part official SPICE card is eligible. The second browser
    # session opens ADI's license page and performs the real consent click.
    return download_model(part, exact[0].url, root, accept_license=True,
                          timeout_seconds=timeout, browser_executable=browser_executable,
                          headed=headed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part_number", help="Exact ADI part number, for example AD8605")
    parser.add_argument("--model-url", required=True, help="Exact official ADI SPICE model/archive link")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for model files and result manifest")
    parser.add_argument("--accept-license", action="store_true",
                        help="Explicit authorization to click Accept and Download for this ADI model")
    parser.add_argument("--browser-executable", help="Optional Brave/Chrome executable; uses a fresh session")
    parser.add_argument("--headed", action="store_true", help="Show the browser window (requires a desktop)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="Total browser timeout (default ADI_BROWSER_TIMEOUT_SECONDS or 180 seconds; maximum 180)")
    args = parser.parse_args()
    try:
        result = download_model(args.part_number, args.model_url, args.output_dir,
                                accept_license=args.accept_license, timeout_seconds=args.timeout,
                                browser_executable=args.browser_executable, headed=args.headed)
    except ValueError as exc:
        parser.error(str(exc))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.output_dir / f"{ADIModelAcquirer._safe_name(args.part_number.upper())}_browser_acquisition.json"
    manifest.write_text(json.dumps(result.to_dict(), indent=2))
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status == AcquisitionStatus.DOWNLOADED else 1


if __name__ == "__main__":
    raise SystemExit(main())
