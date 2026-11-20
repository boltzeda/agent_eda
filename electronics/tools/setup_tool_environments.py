"""Create and install one isolated Python environment per Agent-EDA tool."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
ENVIRONMENTS_ROOT = TOOLS_ROOT / "tool_environments"
TOOL_NAMES = (
    "component_datasheet_prepare",
    "component_datasheet_parse",
    "component_schema_validate",
    "kicad_library_find",
    "kicad_library_generate",
    "kicad_schematic_setup",
    "kicad_schematic_generate",
    "pcb_workflow",
    "acquire_adi_model",
    "acquire_nxp_model",
    "acquire_st_model",
    "acquire_ti_model",
)
DEFAULT_TOOL_PYTHON = {"kicad_schematic_generate": "3.12"}


def _uv() -> str:
    executable = shutil.which("uv")
    if not executable:
        candidate = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        raise RuntimeError("uv is required; install it with the Agent-EDA setup command first")
    return executable


def _base_python(python: Path) -> Path | None:
    if not python.is_file():
        return None
    result = subprocess.run(
        [str(python), "-c", "import sys; print(sys._base_executable)"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    return Path(result.stdout.strip()).resolve()


def _system_kicad_python() -> Path | None:
    candidates = [Path("/usr/bin/python3"), *Path("/usr/bin").glob("python3.*")]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        result = subprocess.run(
            [str(candidate), "-c", "import pcbnew"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return candidate
    return None


def probe_pcbnew(python: Path, tools_root: Path = TOOLS_ROOT) -> str:
    """Check KiCad with the same binding and DLL discovery used at runtime."""
    code = (
        "from pcb_workflow.kicad_environment import import_pcbnew; "
        "module = import_pcbnew(); "
        "print(getattr(module, 'GetBuildVersion', lambda: 'available')())"
    )
    result = subprocess.run(
        [str(python), "-c", code], cwd=tools_root,
        capture_output=True, text=True, errors="replace", check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"KiCad pcbnew could not load in {python}. "
            "Install a KiCad version with Python bindings, then rerun setup. "
            "For a custom installation set KICAD_ROOT or KICAD_PYTHON_PATH and KICAD_BIN; "
            "if the binding uses another Python version, pass --pcb-python. "
            f"Details: {result.stderr[-2200:]}"
        )
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "available"


def setup_tool(
    tool_name: str,
    python_version: str,
    *,
    pcb_python: str | None = None,
    recreate: bool = False,
) -> None:
    directory = ENVIRONMENTS_ROOT / tool_name
    requirements = directory / "requirements.txt"
    environment = directory / ".venv"
    if not requirements.is_file():
        raise FileNotFoundError(f"Missing requirements file: {requirements}")
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = _uv()
    if recreate and environment.exists():
        shutil.rmtree(environment)
    if tool_name == "pcb_workflow" and pcb_python and python.is_file():
        requested = Path(pcb_python).expanduser().resolve()
        current = _base_python(python)
        if current != requested:
            if environment.is_symlink():
                raise RuntimeError(f"Cannot recreate a linked PCB environment: {environment}")
            print(f"Recreating pcb_workflow with {requested}...", flush=True)
            shutil.rmtree(environment)
    if not python.is_file():
        selected_python = pcb_python if tool_name == "pcb_workflow" and pcb_python else python_version
        command = [uv, "venv", "--python", selected_python]
        if tool_name == "pcb_workflow" and not pcb_python and sys.platform.startswith("linux"):
            system_python = _system_kicad_python()
            if system_python:
                command[command.index("--python") + 1] = str(system_python)
        if tool_name == "pcb_workflow":
            # pcbnew is normally supplied by the host KiCad package, not PyPI.
            # A private venv still needs visibility into that interpreter's
            # system packages to import its native binding.
            command.append("--system-site-packages")
        command.append(str(environment))
        subprocess.run(command, check=True)
    if tool_name == "pcb_workflow":
        print(f"KiCad pcbnew: {probe_pcbnew(python, TOOLS_ROOT)}", flush=True)
    substantive = [
        line.strip() for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if substantive:
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), "-r", str(requirements)],
            cwd=directory,
            check=True,
        )
    print(f"Ready: {tool_name} -> {python}", flush=True)


def main() -> int:
    global TOOLS_ROOT, ENVIRONMENTS_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", choices=TOOL_NAMES, help="set up one tool; default sets up all 12")
    parser.add_argument(
        "--python", default=None,
        help="override Python for newly created environments (defaults vary by tool)",
    )
    parser.add_argument("--tools-root", type=Path, default=TOOLS_ROOT,
                        help="tools directory in an assembled build (default: this source tree)")
    parser.add_argument("--pcb-python", help="Python executable matching the host KiCad pcbnew binding")
    parser.add_argument("--recreate", action="store_true", help="rebuild one tool environment from scratch")
    args = parser.parse_args()
    TOOLS_ROOT = args.tools_root.resolve()
    ENVIRONMENTS_ROOT = TOOLS_ROOT / "tool_environments"
    if args.recreate and not args.tool:
        parser.error("--recreate requires --tool so it cannot remove all tool environments accidentally")
    selected = (args.tool,) if args.tool else TOOL_NAMES
    for index, name in enumerate(selected, 1):
        print(f"Tool environment {index}/{len(selected)}: {name}...", flush=True)
        setup_tool(name, args.python or DEFAULT_TOOL_PYTHON.get(name, '3.11'),
                   pcb_python=args.pcb_python, recreate=args.recreate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
