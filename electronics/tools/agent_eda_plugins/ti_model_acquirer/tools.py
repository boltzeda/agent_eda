"""Hermes-facing tool wrapper for the TI model acquirer.

This wrapper deliberately does not call or modify ``model-file-tool``.  It
uses the TI acquirer's standard-library implementation directly and returns
only its JSON manifest to the agent context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result
from tools.tool_environment import run_plugin_handler

from tools.mode_acquisation.ti_model_acquirer.service import TIModelAcquirer


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "generated" / "ti_model_acquirer"
_SUPPORTED_ASSET_TYPES = ("pspice", "spice", "tina-ti", "simplis")


def _handle_acquire_ti_model_in_environment(args: dict, **_kw: Any) -> str:
    """Acquire an official public TI model and return its evidence manifest."""
    part_number = str(args.get("part_number") or "").strip()
    asset_type = str(args.get("asset_type") or "pspice").strip().lower()
    if not part_number:
        return tool_error("part_number is required and must be an exact TI manufacturer part number.")
    if asset_type not in _SUPPORTED_ASSET_TYPES:
        return tool_error(
            "asset_type must be one of: " + ", ".join(_SUPPORTED_ASSET_TYPES) + "."
        )

    try:
        result = TIModelAcquirer(_DEFAULT_OUTPUT_DIR).acquire(part_number, asset_type)
    except Exception as exc:  # The tool boundary must always return a Hermes result.
        return tool_error(f"TI model acquisition failed: {exc}")
    return tool_result(json.dumps(result.to_dict(), indent=2))


def _handle_acquire_ti_model(args: dict, **_kw: Any) -> str:
    return run_plugin_handler(
        "acquire_ti_model", "ti_model_acquirer.tools", "_handle_acquire_ti_model_in_environment", args,
        timeout_seconds=900,
    )


ACQUIRE_TI_MODEL_SCHEMA: dict[str, Any] = {
    "name": "acquire_ti_model",
    "description": (
        "Retrieve a publicly downloadable official Texas Instruments simulation model for an "
        "exact TI part number. It only accesses ti.com, does not use or alter the generic "
        "model resolver, and never bypasses authentication, licensing, payment, or third-party "
        "access controls. Returns a JSON manifest with status, reason code, official URLs, saved "
        "files, SHA-256 digests, individual TI model resources, exact-part coverage, and family-series coverage. "
        "For a TI family product page, it downloads every public model card in the requested format."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "part_number": {
                "type": "string",
                "description": "Exact TI manufacturer part number, for example LM741 or TPS63031.",
            },
            "asset_type": {
                "type": "string",
                "enum": list(_SUPPORTED_ASSET_TYPES),
                "description": "Requested official TI model format. Defaults to pspice.",
            },
        },
        "required": ["part_number"],
    },
}
