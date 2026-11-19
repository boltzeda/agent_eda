"""Agent-facing tools for setting up and generating native KiCad schematics."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tools.registry import tool_error, tool_result

from tools.tool_environment import run_plugin_handler


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


def handle_kicad_schematic_setup(args: dict[str, Any], **_kwargs: Any) -> str:
    return run_plugin_handler(
        "kicad_schematic_setup", "tools.kicad_generation.tools",
        "handle_kicad_schematic_setup", args, timeout_seconds=1800,
    )


def handle_kicad_schematic_generate(args: dict[str, Any], **_kwargs: Any) -> str:
    return run_plugin_handler(
        "kicad_schematic_generate", "tools.kicad_generation.tools",
        "handle_kicad_schematic_generate", args, timeout_seconds=1830,
    )

