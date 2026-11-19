"""Hermes-facing tool wrapper for the ST model acquirer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result
from tools.tool_environment import run_plugin_handler

from tools.mode_acquisation.st_model_acquirer.service import STModelAcquirer


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "generated" / "st_model_acquirer"
_SUPPORTED_ASSET_TYPES = (
    "spice", "pspice", "ltspice", "simplis", "simetrix", "eldo",
)


def _handle_acquire_st_model_in_environment(args: dict, **_kw: Any) -> str:
    """Acquire an official public ST model and return its evidence manifest."""
    part_number = str(args.get("part_number") or "").strip()
    asset_type = str(args.get("asset_type") or "spice").strip().lower()
    if not part_number:
        return tool_error(
            "part_number is required and must be an exact STMicroelectronics part number."
        )
    if asset_type not in _SUPPORTED_ASSET_TYPES:
        return tool_error(
            "asset_type must be one of: " + ", ".join(_SUPPORTED_ASSET_TYPES) + "."
        )

    try:
        result = STModelAcquirer(_DEFAULT_OUTPUT_DIR).acquire(part_number, asset_type, product_url=args.get("product_url"))
    except Exception as exc:  # The Hermes boundary must always return a tool result.
        return tool_error(f"ST model acquisition failed: {exc}")
    return tool_result(json.dumps(result.to_dict(), indent=2))


def _handle_acquire_st_model(args: dict, **_kw: Any) -> str:
    return run_plugin_handler(
        "acquire_st_model", "st_model_acquirer.tools", "_handle_acquire_st_model_in_environment", args,
        timeout_seconds=900,
    )


ACQUIRE_ST_MODEL_SCHEMA: dict[str, Any] = {
    "name": "acquire_st_model",
    "description": (
        "Retrieve publicly downloadable official STMicroelectronics simulation models for "
        "an exact ST part number. It accesses only official st.com resources, does not use "
        "or alter the generic model resolver, and never bypasses authentication, licensing, "
        "payment, or third-party access controls. Returns a JSON evidence manifest with "
        "official URLs, saved files, SHA-256 digests, exact-part coverage, family-series "
        "coverage, and per-resource errors."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "part_number": {
                "type": "string",
                "description": (
                    "Exact STMicroelectronics manufacturer part number, for example "
                    "A6986F, TSC101, or STL110N8F8."
                ),
            },
            "asset_type": {
                "type": "string",
                "enum": list(_SUPPORTED_ASSET_TYPES),
                "description": "Requested official ST model format. Defaults to spice.",
            },
            "product_url": {
                "type": "string",
                "description": "Optional official ST product page for the exact part; searched when the resource index has no model.",
            },
        },
        "required": ["part_number"],
    },
}
