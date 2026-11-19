"""Agent EDA tool bridge for the standalone PCB workflow CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tools.tool_environment import TOOLS_ROOT, isolated_tool_env, python_for


_WORKFLOW_DIR = TOOLS_ROOT / "pcb_workflow"
_TOOLS_ROOT = TOOLS_ROOT
_WORKFLOW_PYTHON = python_for('pcb_workflow')


def run_stage(args: dict[str, Any], **kwargs: Any) -> str:
    """Run exactly one workflow stage through its dedicated virtualenv."""
    stage = str(args.get("stage", "")).strip()
    if stage not in {"doctor", "preprocess", "place", "route"}:
        return json.dumps({"ok": False, "error": "stage must be doctor, preprocess, place, or route"})

    if stage == "route" and args.get("routing_approved") is not True:
        return json.dumps({
            "ok": False,
            "error": "Routing is blocked until the user has reviewed placement and explicitly approved routing.",
        })

    cli_args = args.get("arguments", [])
    if not isinstance(cli_args, list) or not all(isinstance(item, str) for item in cli_args):
        return json.dumps({"ok": False, "error": "arguments must be an array of strings"})

    python = _WORKFLOW_PYTHON
    if not python.is_file():
        return json.dumps({"ok": False, "error": f"Workflow interpreter not found: {python}"})

    command = [str(python), "-E", "-s", "-m", "pcb_workflow", stage, *cli_args]
    try:
        result = subprocess.run(
            command,
            cwd=_TOOLS_ROOT,
            env=isolated_tool_env(),
            shell=False,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": "Workflow stage exceeded the 600-second limit."})
    except OSError as exc:
        return json.dumps({"ok": False, "error": f"Could not start workflow stage: {exc}"})

    return json.dumps({
        "ok": result.returncode == 0,
        "stage": stage,
        "exit_code": result.returncode,
        "stdout": result.stdout[-24000:],
        "stderr": result.stderr[-12000:],
    })


_TOOL_SCHEMA = {
    "name": "pcb_workflow",
    "description": (
        "Run exactly one stage of the PCB workflow. Preprocess and place are separate calls; "
        "placement must be reviewed before routing. Call route only after the user explicitly "
        "approves routing the reviewed placed board, and set routing_approved=true only then. "
        "Use arguments as the exact CLI arguments documented by the workflow."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "stage": {
                "type": "string",
                "enum": ["doctor", "preprocess", "place", "route"],
                "description": "One stage per call; never chain placement into routing.",
            },
            "arguments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Arguments passed to that stage's existing command-line parser.",
            },
            "routing_approved": {
                "type": "boolean",
                "description": "Must be true only after the user explicitly approves routing the reviewed placement.",
            },
        },
        "required": ["stage", "arguments"],
        "additionalProperties": False,
    },
}


def register(ctx: Any) -> None:
    """Register the stage dispatcher as an Agent EDA model tool."""
    ctx.register_tool(
        name="pcb_workflow",
        toolset="pcb_workflow",
        schema=_TOOL_SCHEMA,
        handler=run_stage,
        description="Run one reviewed stage of the standalone KiCad PCB workflow.",
        emoji="▦",
    )
