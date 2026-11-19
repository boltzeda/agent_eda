"""Isolated Python execution support for locally installed Agent-EDA tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from tools.environments.local import build_subprocess_env

TOOLS_ROOT = Path(__file__).resolve().parent
ENVIRONMENTS_ROOT = TOOLS_ROOT / "tool_environments"
PLUGIN_ROOT = get_hermes_home() / "plugins"


def python_for(tool_name: str) -> Path:
    """Return the interpreter assigned to a tool's private environment."""
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return ENVIRONMENTS_ROOT / tool_name / ".venv" / executable


def isolated_tool_env(*import_roots: Path) -> dict[str, str]:
    """Keep parent Python packages out of a tool's independently versioned venv."""
    environment = build_subprocess_env()
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE"):
        environment.pop(key, None)
    environment["PYTHONNOUSERSITE"] = "1"
    if import_roots:
        environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in import_roots)
    return environment


def run_plugin_handler(
    tool_name: str,
    module_name: str,
    handler_name: str,
    args: dict[str, Any],
    *,
    timeout_seconds: int = 1800,
) -> str:
    """Run a plugin handler in the named tool environment and return its JSON result."""
    python = python_for(tool_name)
    if not python.is_file():
        return json.dumps({
            "success": False,
            "error": f"Environment for {tool_name} is not installed. Run `python tools/setup_tool_environments.py --tool {tool_name}`.",
        })

    runner = (
        "import importlib,json,sys; "
        "module=importlib.import_module(sys.argv[1]); "
        "handler=getattr(module,sys.argv[2]); "
        "args=json.load(sys.stdin); "
        "result=handler(args); "
        "print(result if isinstance(result,str) else json.dumps(result,ensure_ascii=False))"
    )
    environment = isolated_tool_env(TOOLS_ROOT.parent, PLUGIN_ROOT)
    try:
        result = subprocess.run(
            [str(python), "-c", runner, module_name, handler_name],
            cwd=TOOLS_ROOT.parent,
            env=environment,
            input=json.dumps(args, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"success": False, "error": f"{tool_name} exceeded its {timeout_seconds}-second limit."})
    except OSError as exc:
        return json.dumps({"success": False, "error": f"Could not start {tool_name}: {exc}"})

    output = result.stdout.strip()
    if result.returncode == 0 and output:
        return output
    return json.dumps({
        "success": False,
        "error": f"{tool_name} failed in its private environment (exit {result.returncode}).",
        "stdout": output[-12000:],
        "stderr": result.stderr[-12000:],
    }, ensure_ascii=False)
