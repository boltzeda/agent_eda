"""ERC and DRC validation for generated schematics and PCBs.

Uses ``kicad-cli`` subprocess — no KiCAD GUI or pcbnew required.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from engine.backends.design_rules import _find_kicad_cli, run_drc

logger = logging.getLogger(__name__)


def run_erc(schematic_path: str | Path) -> Dict[str, Any]:
    """Run ERC on a .kicad_sch file via ``kicad-cli sch erc``."""
    schematic_path = Path(schematic_path)
    if not schematic_path.exists():
        return {"success": False, "message": f"Schematic not found: {schematic_path}", "errors": -1, "warnings": -1}

    kicad_cli = _find_kicad_cli()
    if not kicad_cli:
        return {"success": False, "message": "kicad-cli not found", "errors": -1, "warnings": -1}

    report_path = schematic_path.with_suffix(".erc.rpt")
    cmd = [kicad_cli, "sch", "erc", "--format", "report", "--output", str(report_path), str(schematic_path)]
    logger.info(f"Running ERC: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"success": False, "message": f"ERC failed: {e}", "errors": -1, "warnings": -1}

    errors, warnings, messages = 0, 0, []
    if report_path.exists():
        text = report_path.read_text(encoding="utf-8")
        errors, warnings, messages = _parse_erc_report(text)

    return {
        "success": True,
        "message": f"ERC complete: {errors} errors, {warnings} warnings",
        "errors": errors,
        "warnings": warnings,
        "messages": messages,
        "report_path": str(report_path),
    }


def _parse_erc_report(text: str) -> tuple:
    """Parse a KiCAD ERC text report into (errors, warnings, messages)."""
    errors, warnings = 0, 0
    messages: List[Dict[str, str]] = []

    m = re.search(r"ERC messages:\s+\d+\s+Errors\s+(\d+)\s+Warnings\s+(\d+)", text)
    if m:
        errors, warnings = int(m.group(1)), int(m.group(2))

    for m in re.finditer(r"\[(\w+)\]:\s+(.+?)(?:\s*;\s*(error|warning))?\n", text):
        messages.append({"type": m.group(1), "severity": m.group(3) or "warning", "message": m.group(2).strip()})

    return errors, warnings, messages


def validate_schematic(
    schematic_path: str | Path, *, max_errors: int = 0, max_warnings: int = 50,
) -> Dict[str, Any]:
    """Run ERC and return pass/fail result."""
    erc = run_erc(schematic_path)
    if not erc["success"]:
        return {"passed": False, "erc": erc, "failures": [erc.get("message", "ERC could not run")]}

    failures: List[str] = []
    if erc["errors"] > max_errors:
        failures.append(f"ERC errors ({erc['errors']}) exceed limit ({max_errors})")
    if erc["warnings"] > max_warnings:
        failures.append(f"ERC warnings ({erc['warnings']}) exceed limit ({max_warnings})")

    return {"passed": len(failures) == 0, "erc": erc, "failures": failures}
