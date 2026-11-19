"""Manage and invoke the plugin's private Circuit Synth Python environment."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parent
VENV_DIR = Path(__file__).resolve().parents[1] / "tool_environments" / "kicad_schematic_generate" / ".venv"
REQUIREMENTS = VENV_DIR.parent / "requirements.txt"


def python_in_venv() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def setup_environment() -> subprocess.CompletedProcess[str]:
    """Create the local venv and install the bundled Circuit Synth project."""
    if not python_in_venv().exists():
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    uv = shutil.which("uv")
    if not uv:
        candidate = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
        if candidate.is_file():
            uv = str(candidate)
    if not uv:
        raise FileNotFoundError("uv is required to install this tool's requirements")
    return subprocess.run(
        [uv, "pip", "install", "--python", str(python_in_venv()), "-r", str(REQUIREMENTS)],
        cwd=REQUIREMENTS.parent,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )


def generate_from_script(script_path: str, timeout_seconds: int = 900) -> subprocess.CompletedProcess[str]:
    """Run a project-local generator script using the plugin's private Python."""
    supplied_path = Path(script_path).expanduser()
    if not supplied_path.is_absolute():
        raise ValueError("script_path must be an absolute path visible to the Agent-EDA host")
    script = supplied_path.resolve()
    if not script.is_file() or script.suffix.lower() != ".py":
        raise ValueError("script_path must point to an existing Python generator script")
    if not python_in_venv().is_file():
        raise RuntimeError("Circuit Synth environment is not set up; call kicad_schematic_setup first")
    import_check = subprocess.run(
        [str(python_in_venv()), "-c", "import circuit_synth"],
        cwd=REQUIREMENTS.parent,
        env={**os.environ, "PYTHONPATH": str(PLUGIN_DIR / "vendor" / "circuit-synth" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    if import_check.returncode != 0:
        raise RuntimeError(
            "Circuit Synth is not installed in the plugin environment; "
            "rerun kicad_schematic_setup. " + import_check.stderr[-2000:]
        )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PLUGIN_DIR / "vendor" / "circuit-synth" / "src")
    return subprocess.run(
        [str(python_in_venv()), str(script)],
        cwd=script.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
