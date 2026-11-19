"""Hermes-facing tool wrapper for the standalone NXP model acquirer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

from .service import NXPModelAcquirer


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "generated" / "nxp_model_acquirer"
_SUPPORTED_ASSET_TYPES = ("spice", "pspice", "simplis")


def _handle_acquire_nxp_model(args: dict, **_kw: Any) -> str:
    """Acquire an official public NXP model and return its evidence manifest."""
    part_number = str(args.get("part_number") or "").strip()
    asset_type = str(args.get("asset_type") or "spice").strip().lower()
    if not part_number:
        return tool_error(
            "part_number is required and must be an exact NXP manufacturer part number."
        )
    if asset_type not in _SUPPORTED_ASSET_TYPES:
        return tool_error(
            "asset_type must be one of: " + ", ".join(_SUPPORTED_ASSET_TYPES) + "."
        )

    try:
        result = NXPModelAcquirer(_DEFAULT_OUTPUT_DIR).acquire(part_number, asset_type)
    except Exception as exc:  # The Hermes boundary must always return a tool result.
        return tool_error(f"NXP model acquisition failed: {exc}")
    return tool_result(json.dumps(result.to_dict(), indent=2))


ACQUIRE_NXP_MODEL_SCHEMA: dict[str, Any] = {
    "name": "acquire_nxp_model",
    "description": (
        "Retrieve a publicly downloadable official NXP Semiconductors SPICE, PSpice, "
        "or SIMPLIS model for an exact NXP part number or family. It accesses only "
        "nxp.com and never bypasses authentication, license, payment, or third-party "
        "controls. Returns a JSON evidence manifest with status, reason code, official "
        "URLs, saved files, SHA-256 digests, and exact-part/family coverage."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "part_number": {
                "type": "string",
                "description": "Exact NXP part or family name, for example BF862 or BFU520.",
            },
            "asset_type": {
                "type": "string",
                "enum": list(_SUPPORTED_ASSET_TYPES),
                "description": "Requested official model format. Defaults to spice.",
            },
        },
        "required": ["part_number"],
    },
}
