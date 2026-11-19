"""Agent-facing tools for setting up and generating native KiCad schematics."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

from .runtime import generate_from_script, setup_environment


KICAD_SCHEMATIC_SETUP_SCHEMA: dict[str, Any] = {
    "name": "kicad_schematic_setup",
    "description": (
        "Create the KiCad generation plugin's private Python environment and install "
        "its bundled Circuit Synth source and dependencies. Run this once before generation."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

KICAD_SCHEMATIC_GENERATE_SCHEMA: dict[str, Any] = {
    "name": "kicad_schematic_generate",
    "description": (
        "Run a project-local generate.py with the plugin's isolated Circuit Synth Python "
        "environment. The script should write its KiCad project and schematic beside itself."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "script_path": {
                "type": "string",
                "description": "Absolute path to the project's authored generate.py file, visible on the Agent-EDA host.",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1800,
                "default": 900,
                "description": "Maximum time allowed for schematic generation.",
            },
        },
        "required": ["script_path"],
    },
}


def handle_kicad_schematic_setup(_args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        result = setup_environment()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return tool_error(f"Could not set up the KiCad generation environment: {exc}")
    payload = {
        "success": result.returncode == 0,
        "python": str(_python_path()),
        "returncode": result.returncode,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }
    return tool_result(json.dumps(payload, indent=2))


def handle_kicad_schematic_generate(args: dict[str, Any], **_kwargs: Any) -> str:
    script_path = str(args.get("script_path") or "").strip()
    if not script_path:
        return tool_error("script_path is required and must point to the project's generate.py.")
    timeout = args.get("timeout_seconds", 900)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 1800:
        return tool_error("timeout_seconds must be an integer from 1 through 1800.")
    try:
        result = generate_from_script(script_path, timeout)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return tool_error(f"KiCad schematic generation could not run: {exc}")
    payload = {
        "success": result.returncode == 0,
        "script_path": str(Path(script_path).expanduser().resolve()),
        "returncode": result.returncode,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }
    return tool_result(json.dumps(payload, indent=2))


def _python_path():
    from .runtime import python_in_venv

    return python_in_venv()
