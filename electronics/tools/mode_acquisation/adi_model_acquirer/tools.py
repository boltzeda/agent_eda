"""Hermes-facing tool wrapper for the ADI model acquirer.

This wrapper deliberately does not call or modify ``model-file-tool``.  It
uses the ADI acquirer's standard-library implementation directly and returns
only its JSON manifest to the agent context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

from .service import ADIModelAcquirer
from .status import AcquisitionStatus, ReasonCode


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "generated" / "adi_model_acquirer"
_SUPPORTED_ASSET_TYPES = ("spice", "pspice", "ltspice", "simplis", "ibis")


def _try_exact_mpn_media_url(part_number: str, result, asset_type: str):
    """Try ADI's conventional exact-MPN .cir media name after page access fails.

    This is a constrained discovery fallback, not a license bypass: the browser
    opens ADI's license page and clicks its real consent button, and the saved
    file must declare the requested exact part before it is reported downloaded.
    """
    if (
        result.status != AcquisitionStatus.ERROR
        or result.reason_code != ReasonCode.NETWORK_ERROR
        or asset_type not in {"spice", "pspice"}
    ):
        return result
    from .browser_download import download_model

    candidate_url = (
        "https://www.analog.com/media/en/simulation-models/spice-models/"
        f"{part_number.strip().lower()}.cir"
    )
    candidate = download_model(
        part_number, candidate_url, _DEFAULT_OUTPUT_DIR, accept_license=True
    )
    if candidate.status == AcquisitionStatus.DOWNLOADED:
        return candidate
    result.resource_errors.append({
        "download_url": candidate_url,
        "reason_code": candidate.reason_code.value if candidate.reason_code else None,
        "reason": candidate.reason,
    })
    result.reason = (
        f"{result.reason or 'ADI model discovery failed'}; exact-MPN official media "
        f"candidate also failed validation/download: {candidate.reason or candidate.status.value}"
    )
    return result


def _handle_acquire_adi_model(args: dict, **_kw: Any) -> str:
    """Acquire an official public ADI model and return its evidence manifest."""
    part_number = str(args.get("part_number") or "").strip()
    asset_type = str(args.get("asset_type") or "spice").strip().lower()
    if not part_number:
        return tool_error("part_number is required and must be an exact ADI manufacturer part number.")
    if asset_type not in _SUPPORTED_ASSET_TYPES:
        return tool_error(
            "asset_type must be one of: " + ", ".join(_SUPPORTED_ASSET_TYPES) + "."
        )

    try:
        acquirer = ADIModelAcquirer(_DEFAULT_OUTPUT_DIR)
        local_file = str(args.get("local_model_file") or "").strip()
        source_url = str(args.get("source_url") or "").strip()
        product_url = str(args.get("product_url") or "").strip()
        use_browser = args.get("use_browser") is True
        accept_license = args.get("accept_license") is True
        if product_url and (source_url or local_file):
            return tool_error("product_url is for online product-page discovery; do not combine it with source_url or local_model_file")
        if product_url:
            source_url = product_url
            use_browser = True
            accept_license = True
        # Hermes may discover the exact model URL after product-page retrieval
        # encounters ADI's license interstitial. A URL without a local file is
        # a website download, not an incomplete manual import.
        if source_url and not local_file:
            use_browser = True
            accept_license = True
        if accept_license and not use_browser:
            return tool_error("accept_license requires use_browser=true and a specific official source_url")
        if use_browser:
            if local_file or not source_url or asset_type not in {"spice", "pspice"}:
                return tool_error("Browser acquisition requires source_url, SPICE format, and no local_model_file")
            from .browser_download import download_from_product_page, download_model
            if "/media/en/simulation-models/spice-models/" in source_url.lower():
                result = download_model(part_number, source_url, _DEFAULT_OUTPUT_DIR,
                                        accept_license=accept_license)
            else:
                result = download_from_product_page(
                    part_number, source_url, _DEFAULT_OUTPUT_DIR, asset_type=asset_type
                )
                result = _try_exact_mpn_media_url(part_number, result, asset_type)
        elif bool(local_file) != bool(source_url):
            return tool_error("local_model_file and source_url must be supplied together for a user-provided download")
        elif local_file:
            source = Path(local_file).expanduser()
            source = (source if source.is_absolute() else _PROJECT_ROOT / source).resolve()
            if not any(source.is_relative_to((_PROJECT_ROOT / folder).resolve()) for folder in ("generated", "output")):
                return tool_error("Copy the user-provided model into the project's generated/ or output/ directory before importing")
            result = acquirer.import_model(part_number, source, source_url, asset_type)
        else:
            result = acquirer.acquire(part_number, asset_type)
            # If the normal official-page flow finds the exact model but ADI
            # returns its consent page, complete the download through the
            # isolated browser in this same tool call.
            if (
                result.status == AcquisitionStatus.ACCESS_REQUIRED
                and result.reason_code == ReasonCode.LICENSE_ACCEPTANCE_REQUIRED
                and result.download_url
                and asset_type in {"spice", "pspice"}
            ):
                from .browser_download import download_model
                result = download_model(part_number, result.download_url, _DEFAULT_OUTPUT_DIR,
                                        accept_license=True)
            elif (result.status == AcquisitionStatus.ERROR
                  and result.reason_code == ReasonCode.NETWORK_ERROR
                  and asset_type in {"spice", "pspice"}):
                # The common ADI failure is a slow product-page request. Retry
                # discovery in a real browser, then follow the official consent
                # page if the product page exposes a matching model card.
                from .browser_download import download_from_product_page
                result = download_from_product_page(
                    part_number,
                    f"https://www.analog.com/en/products/{part_number.lower()}.html",
                    _DEFAULT_OUTPUT_DIR,
                    asset_type=asset_type,
                )
                result = _try_exact_mpn_media_url(part_number, result, asset_type)
    except Exception as exc:  # The tool boundary must always return a Hermes result.
        return tool_error(f"ADI model acquisition failed: {exc}")
    return tool_result(json.dumps(result.to_dict(), indent=2))


ACQUIRE_ADI_MODEL_SCHEMA: dict[str, Any] = {
    "name": "acquire_adi_model",
    "description": (
        "Retrieve an official Analog Devices simulation model for an "
        "exact ADI part number. It only accesses analog.com, does not use or alter the generic "
        "model resolver, and never bypasses authentication, licensing, payment, or third-party "
        "access controls. Returns a JSON manifest with status, reason code, official URLs, saved "
        "files, SHA-256 digests, individual ADI model resources, exact-part coverage, and family-series coverage. "
        "For an ADI family product page, it downloads every public model card in the requested format."
        " Normally provide only the exact part_number and asset_type: the plugin resolves official "
        "model links itself, retries blocked product-page discovery with a validated exact-MPN "
        "official media candidate, and clicks ADI's actual consent button automatically. "
        "Use product_url only for a known official product page; use source_url only for a known "
        "direct official model URL or a local import. Never treat a product page as a model URL "
        "or bypass or alter the page. "
        "A user-supplied local_model_file plus source_url imports without network access and "
        "preserves user-declared provenance; it is not a fresh website download."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "part_number": {
                "type": "string",
                "description": "Exact ADI manufacturer part number, for example AD8605, AD620, or ADA4530-1.",
            },
            "asset_type": {
                "type": "string",
                "enum": list(_SUPPORTED_ASSET_TYPES),
                "description": "Requested official ADI model format. Defaults to spice.",
            },
            "source_url": {
                "type": "string",
                "description": "Direct official HTTPS analog.com model media URL. With local_model_file, imports a supplied download; without it, downloads through ADI's consent page. Do not put a product-page URL here.",
            },
            "product_url": {
                "type": "string",
                "description": "Optional exact official ADI product-page URL for online model discovery. Do not combine with source_url or local_model_file. Normally omit it and let the plugin discover the model from part_number.",
            },
            "use_browser": {
                "type": "boolean",
                "description": "Use the isolated license-button downloader. Default false.",
            },
            "accept_license": {
                "type": "boolean",
                "description": "Explicitly select the isolated browser route. A source_url without local_model_file also selects this route automatically.",
            },
            "local_model_file": {
                "type": "string",
                "description": "User-supplied model under project generated/ or output/; requires source_url.",
            },
        },
        "required": ["part_number"],
    },
}
