"""Design rule checking for PCB layouts via kicad-cli.

Adapted from KiCAD-MCP-Server for standalone use. The pcbnew import is
optional — DRC runs via ``kicad-cli pcb drc`` subprocess.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import pcbnew
    _HAS_PCBNEW = True
except ImportError:
    pcbnew = None
    _HAS_PCBNEW = False


def _find_kicad_cli() -> Optional[str]:
    """Locate the kicad-cli executable."""
    cli_name = "kicad-cli.exe" if platform.system() == "Windows" else "kicad-cli"
    cli_path = shutil.which(cli_name)
    if cli_path:
        return cli_path

    if platform.system() == "Windows":
        common_paths = [
            r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
            r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
            r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
        ]
    elif platform.system() == "Darwin":
        common_paths = [
            "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
            "/usr/local/bin/kicad-cli",
        ]
    else:
        common_paths = [
            "/usr/bin/kicad-cli",
            "/usr/local/bin/kicad-cli",
        ]

    for path in common_paths:
        if os.path.exists(path):
            return path
    return None


def run_drc(board_file: str, report_path: Optional[str] = None) -> Dict[str, Any]:
    """Run PCB Design Rule Check via kicad-cli."""
    board_file = os.path.abspath(board_file)
    if not os.path.exists(board_file):
        return {"success": False, "message": "Board file not found", "errorDetails": board_file}

    kicad_cli = _find_kicad_cli()
    if not kicad_cli:
        return {"success": False, "message": "kicad-cli not found", "errorDetails": "Install KiCAD 8.0+"}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json_output = tmp.name

    try:
        cmd = [kicad_cli, "pcb", "drc", "--format", "json", "--output", json_output, "--units", "mm", board_file]
        logger.info(f"Running DRC: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            return {"success": False, "message": "DRC command failed", "errorDetails": result.stderr}

        with open(json_output, "r", encoding="utf-8") as f:
            drc_data = json.load(f)

        violations = []
        violation_counts: Dict[str, int] = {}
        severity_counts = {"error": 0, "warning": 0, "info": 0}

        for v in drc_data.get("violations", []):
            vtype = v.get("type", "unknown")
            vsev = v.get("severity", "error")
            violations.append({"type": vtype, "severity": vsev, "message": v.get("description", "")})
            violation_counts[vtype] = violation_counts.get(vtype, 0) + 1
            if vsev in severity_counts:
                severity_counts[vsev] += 1

        board_dir = os.path.dirname(board_file)
        board_name = os.path.splitext(os.path.basename(board_file))[0]
        vf = os.path.join(board_dir, f"{board_name}_drc_violations.json")
        with open(vf, "w", encoding="utf-8") as f:
            json.dump({"board": board_file, "total_violations": len(violations), "violations": violations}, f, indent=2)

        return {
            "success": True,
            "message": f"Found {len(violations)} DRC violations",
            "summary": {"total": len(violations), "by_severity": severity_counts, "by_type": violation_counts},
            "violationsFile": vf,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "DRC timed out"}
    except Exception as e:
        return {"success": False, "message": "Failed to run DRC", "errorDetails": str(e)}
    finally:
        if os.path.exists(json_output):
            os.unlink(json_output)
