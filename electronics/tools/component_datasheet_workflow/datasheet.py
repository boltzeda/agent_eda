"""Acquire and inspect a datasheet PDF without inferring component facts."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from urllib.parse import urlparse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader


SOURCE_TYPES = {"manufacturer", "authorized_distributor_mirror", "user_provided"}
MAX_PDF_BYTES = 100 * 1024 * 1024


def _identity_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _download(url: str, target: Path) -> None:
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("pdf_url must use http or https")
    request = urllib.request.Request(url, headers={"User-Agent": "component-datasheet-workflow/0.1"})
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                raise ValueError(f"datasheet exceeds the {MAX_PDF_BYTES // (1024 * 1024)} MB size limit")
            output.write(chunk)


def prepare_datasheet(
    *,
    manufacturer: str,
    mpn: str,
    output_dir: str | Path,
    pdf_path: str | Path | None = None,
    pdf_url: str | None = None,
    source_type: str | None = None,
) -> dict[str, Any]:
    """Copy/download a PDF, validate its container, and write provenance metadata.

    Identity matching is deliberately reported as evidence for agent review; it
    is not treated as proof when the PDF text layer omits the part identifiers.
    """
    manufacturer = manufacturer.strip()
    mpn = mpn.strip()
    if not manufacturer or not mpn:
        raise ValueError("manufacturer and exact MPN are required")
    if bool(pdf_path) == bool(pdf_url):
        raise ValueError("provide exactly one of pdf_path or pdf_url")

    inferred_source_type = source_type or ("user_provided" if pdf_path else "manufacturer")
    if inferred_source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of: {', '.join(sorted(SOURCE_TYPES))}")
    if inferred_source_type in {"manufacturer", "authorized_distributor_mirror"} and not pdf_url:
        raise ValueError("a manufacturer or authorized-distributor source requires pdf_url")

    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    pdf_file = destination / "datasheet.pdf"
    if pdf_path:
        source = Path(pdf_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"datasheet file not found: {source}")
        if source != pdf_file:
            shutil.copyfile(source, pdf_file)
    else:
        _download(str(pdf_url), pdf_file)

    data = pdf_file.read_bytes()
    if not data.startswith(b"%PDF-"):
        pdf_file.unlink(missing_ok=True)
        raise ValueError("input is not a PDF (missing the PDF file signature)")
    if len(data) > MAX_PDF_BYTES:
        pdf_file.unlink(missing_ok=True)
        raise ValueError("datasheet exceeds the 100 MB size limit")

    try:
        reader = PdfReader(str(pdf_file), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted PDFs are not supported")
        page_count = len(reader.pages)
        if page_count < 1:
            raise ValueError("PDF contains no pages")
        text_parts = [page.extract_text() or "" for page in reader.pages]
        metadata = reader.metadata
    except Exception as exc:
        pdf_file.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"PDF could not be opened: {exc}") from exc

    document_text = "\n".join(text_parts)
    metadata_text = " ".join(str(v) for v in (metadata or {}).values() if v)
    searchable_text = _identity_token(document_text + "\n" + metadata_text)
    manufacturer_found = _identity_token(manufacturer) in searchable_text
    mpn_found = _identity_token(mpn) in searchable_text
    if manufacturer_found and mpn_found:
        identity_status = "text_match_review_required"
    elif mpn_found or manufacturer_found:
        identity_status = "partial_text_match_review_required"
    else:
        identity_status = "no_text_match_review_required"

    digest = hashlib.sha256(data).hexdigest()
    manifest = {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "component": {"manufacturer": manufacturer, "mpn": mpn},
        "datasheet": {
            "file_name": pdf_file.name,
            "path": str(pdf_file),
            "sha256": digest,
            "size_bytes": len(data),
            "page_count": page_count,
            "source_type": inferred_source_type,
            "source_url": pdf_url,
            "pdf_text_identity_check": {
                "status": identity_status,
                "manufacturer_found": manufacturer_found,
                "mpn_found": mpn_found,
                "meaning": "Text matching is a review aid; the agent must confirm the document identity and source."
            },
            "pdf_metadata": {
                "title": str(getattr(metadata, "title", "") or ""),
                "author": str(getattr(metadata, "author", "") or ""),
                "subject": str(getattr(metadata, "subject", "") or "")
            }
        }
    }
    manifest_path = destination / "datasheet_manifest.json"
    _write_json(manifest_path, manifest)
    return {"success": True, "manifest_path": str(manifest_path), **manifest}
