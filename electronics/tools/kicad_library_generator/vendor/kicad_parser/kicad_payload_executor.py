#!/usr/bin/env python3
"""Execute KiCad MCP creator payloads and validate generated library files.

The payloads are produced by scripts/kicad_rule_resolver.py. This script uses
the same Python creator implementations behind the KiCAD-MCP-Server tools,
which makes it useful for local batch generation and CI validation.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
KICAD_MCP_PYTHON = REPO_ROOT / "sym_and_fp_creator" / "python"
PREVIEW_ROOT = REPO_ROOT / "extracted" / "previews"
sys.path.insert(0, str(KICAD_MCP_PYTHON))

from commands.footprint import FootprintCreator  # noqa: E402
from commands.symbol_creator import SymbolCreator  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def execute_symbols(payloads: dict[str, Any], allow_draft: bool) -> dict[str, Any]:
    creator = SymbolCreator()
    results: dict[str, Any] = {}

    for key, entry in payloads["mcp_payloads"]["symbols"].items():
        payload = entry["payload"]
        result = creator.create_symbol(
            library_path=payload["libraryPath"],
            name=payload["name"],
            reference_prefix=payload.get("referencePrefix", "U"),
            description=payload.get("description", ""),
            keywords=payload.get("keywords", ""),
            datasheet=payload.get("datasheet", "~"),
            footprint=payload.get("footprint", ""),
            in_bom=payload.get("inBom", True),
            on_board=payload.get("onBoard", True),
            pins=payload.get("pins", []),
            rectangles=payload.get("rectangles", []),
            polylines=payload.get("polylines", []),
            overwrite=payload.get("overwrite", False),
        )
        results[key] = {
            "tool": "create_symbol",
            "payload_name": payload["name"],
            "result": result,
            "validation": validate_symbol_file(Path(payload["libraryPath"]), payload),
            "preview": export_symbol_preview(Path(payload["libraryPath"]), payload["name"]),
        }

    return results


def execute_footprint(payloads: dict[str, Any], allow_draft: bool) -> dict[str, Any]:
    footprint_entry = payloads["mcp_payloads"]["footprint"]
    validation_required = footprint_entry.get("validation_required", [])
    if validation_required and not allow_draft:
        return {
            "tool": "create_footprint",
            "skipped": True,
            "reason": "Footprint has fields requiring validation. Re-run with --allow-draft-footprint.",
            "validation_required": validation_required,
        }

    payload = footprint_entry["payload"]
    creator = FootprintCreator()
    result = creator.create_footprint(
        library_path=payload["libraryPath"],
        name=payload["name"],
        description=payload.get("description", ""),
        tags=payload.get("tags", ""),
        pads=payload.get("pads", []),
        courtyard=payload.get("courtyard"),
        silkscreen=payload.get("silkscreen"),
        fab_layer=payload.get("fabLayer"),
        ref_position=payload.get("refPosition"),
        value_position=payload.get("valuePosition"),
        overwrite=payload.get("overwrite", False),
    )
    footprint_path = Path(result.get("path", ""))
    return {
        "tool": "create_footprint",
        "skipped": False,
        "result": result,
        "validation": validate_footprint_file(footprint_path, payload)
        if footprint_path
        else {"success": False, "errors": ["No footprint path returned"]},
        "preview": export_footprint_preview(Path(payload["libraryPath"]), payload["name"]),
        "validation_required": validation_required,
    }


def validate_symbol_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not path.exists():
        return {"success": False, "errors": [f"Symbol library not found: {path}"]}

    text = path.read_text(encoding="utf-8")
    if f'(symbol "{payload["name"]}"' not in text:
        errors.append(f"Missing symbol block: {payload['name']}")

    expected_pins = payload.get("pins", [])
    symbol_start = text.find(f'(symbol "{payload["name"]}"')
    symbol_text = text[symbol_start:] if symbol_start >= 0 else text
    for pin in expected_pins:
        if f'(number "{pin["number"]}"' not in symbol_text:
            errors.append(f"Missing pin number {pin['number']} in {payload['name']}")
        if f'(name "{pin["name"]}"' not in symbol_text:
            errors.append(f"Missing pin name {pin['name']} in {payload['name']}")

    return {
        "success": not errors,
        "errors": errors,
        "expected_pin_count": len(expected_pins),
        "library_path": str(path),
    }


def validate_footprint_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {"success": False, "errors": [f"Footprint file not found: {path}"]}

    text = path.read_text(encoding="utf-8")
    if f'(footprint "{payload["name"]}"' not in text:
        errors.append(f"Missing footprint block: {payload['name']}")

    pad_matches = re.findall(r'\(pad\s+"([^"]+)"', text)
    expected_pads = payload.get("pads", [])
    if len(pad_matches) != len(expected_pads):
        errors.append(f"Pad count mismatch: expected {len(expected_pads)}, found {len(pad_matches)}")

    for pad in expected_pads:
        if str(pad["number"]) not in pad_matches:
            errors.append(f"Missing pad number {pad['number']}")

    for rect_name in ["courtyard", "fabLayer", "silkscreen"]:
        if rect_name in payload and not payload[rect_name]:
            errors.append(f"Empty rectangle payload: {rect_name}")

    geometry = validate_footprint_geometry(payload)
    errors.extend(geometry["errors"])
    warnings.extend(geometry["warnings"])

    return {
        "success": not errors,
        "errors": errors,
        "warnings": warnings,
        "expected_pad_count": len(expected_pads),
        "found_pad_numbers": pad_matches,
        "footprint_path": str(path),
        "geometry": geometry,
    }


def normalize_rect(rect: dict[str, Any]) -> dict[str, float]:
    return {
        "x_min": min(float(rect["x1"]), float(rect["x2"])),
        "x_max": max(float(rect["x1"]), float(rect["x2"])),
        "y_min": min(float(rect["y1"]), float(rect["y2"])),
        "y_max": max(float(rect["y1"]), float(rect["y2"])),
    }


def pad_bounds(pad: dict[str, Any]) -> dict[str, float]:
    x = float(pad.get("at", {}).get("x", 0.0))
    y = float(pad.get("at", {}).get("y", 0.0))
    w = float(pad.get("size", {}).get("w", 0.0))
    h = float(pad.get("size", {}).get("h", 0.0))
    return {
        "x_min": x - w / 2.0,
        "x_max": x + w / 2.0,
        "y_min": y - h / 2.0,
        "y_max": y + h / 2.0,
    }


def bounds_overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    return (
        a["x_min"] < b["x_max"]
        and a["x_max"] > b["x_min"]
        and a["y_min"] < b["y_max"]
        and a["y_max"] > b["y_min"]
    )


def validate_footprint_geometry(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    pads = payload.get("pads", [])

    for index, pad in enumerate(pads):
        size = pad.get("size", {})
        if float(size.get("w", 0.0)) <= 0 or float(size.get("h", 0.0)) <= 0:
            errors.append(f"Pad {pad.get('number', index)} has non-positive size")
        at = pad.get("at", {})
        if "x" not in at or "y" not in at:
            errors.append(f"Pad {pad.get('number', index)} missing at.x/at.y")

    for rect_name in ["courtyard", "fabLayer", "silkscreen"]:
        rect = payload.get(rect_name)
        if not rect:
            continue
        try:
            normalized = normalize_rect(rect)
        except (KeyError, TypeError, ValueError):
            errors.append(f"{rect_name} rectangle is malformed")
            continue
        if normalized["x_min"] == normalized["x_max"] or normalized["y_min"] == normalized["y_max"]:
            errors.append(f"{rect_name} rectangle has zero area")

    courtyard = payload.get("courtyard")
    if courtyard and pads:
        court = normalize_rect(courtyard)
        for pad in pads:
            bounds = pad_bounds(pad)
            if (
                bounds["x_min"] < court["x_min"]
                or bounds["x_max"] > court["x_max"]
                or bounds["y_min"] < court["y_min"]
                or bounds["y_max"] > court["y_max"]
            ):
                errors.append(f"Pad {pad.get('number')} is outside courtyard")

    for i, first in enumerate(pads):
        first_bounds = pad_bounds(first)
        for second in pads[i + 1 :]:
            if str(first.get("number")) == str(second.get("number")):
                continue
            if bounds_overlap(first_bounds, pad_bounds(second)):
                warnings.append(
                    f"Pads {first.get('number')} and {second.get('number')} overlap"
                )

    return {"success": not errors, "errors": errors, "warnings": warnings}


def run_preview_command(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "success": proc.returncode == 0,
    }


def export_symbol_preview(library_path: Path, symbol_name: str) -> dict[str, Any]:
    output_dir = PREVIEW_ROOT / "symbols"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "kicad-cli",
        "sym",
        "export",
        "svg",
        str(library_path),
        "--symbol",
        symbol_name,
        "--output",
        str(output_dir),
    ]
    result = run_preview_command(command)
    # KiCad CLI sanitizes '/' in exported filenames to a space.
    preview_name = symbol_name.replace("/", " ")
    result["expected_output"] = str(output_dir / f"{preview_name}_unit1.svg")
    result["output_exists"] = Path(result["expected_output"]).exists()
    result["success"] = bool(result["success"] and result["output_exists"])
    return result


def export_footprint_preview(library_path: Path, footprint_name: str) -> dict[str, Any]:
    output_dir = PREVIEW_ROOT / "footprints"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "kicad-cli",
        "fp",
        "export",
        "svg",
        str(library_path),
        "--footprint",
        footprint_name,
        "--output",
        str(output_dir),
        "--layers",
        "F.Cu,F.Paste,F.Mask,F.SilkS,F.Fab,F.CrtYd",
    ]
    result = run_preview_command(command)
    result["expected_output"] = str(output_dir / f"{footprint_name}.svg")
    result["output_exists"] = Path(result["expected_output"]).exists()
    result["success"] = bool(result["success"] and result["output_exists"])
    return result


def execute(payload_path: Path, report_path: Path, allow_draft: bool) -> dict[str, Any]:
    payloads = load_json(payload_path)
    symbol_results = execute_symbols(payloads, allow_draft=allow_draft)
    footprint_result = execute_footprint(payloads, allow_draft=allow_draft)
    payload_validation = payloads.get("validation_summary", {})

    report = {
        "input_payload": str(payload_path.resolve()),
        "allow_draft_footprint": allow_draft,
        "payload_validation_summary": payload_validation,
        "symbols": symbol_results,
        "footprint": footprint_result,
        "validation_summary": summarize(symbol_results, footprint_result, payload_validation),
    }
    write_json(report_path, report)
    return report


def summarize(
    symbols: dict[str, Any],
    footprint: dict[str, Any],
    payload_validation: dict[str, Any],
) -> dict[str, Any]:
    symbol_ok = all(
        item.get("result", {}).get("success") and item.get("validation", {}).get("success")
        and item.get("preview", {}).get("success")
        for item in symbols.values()
    )
    footprint_created = not footprint.get("skipped", False) and footprint.get("result", {}).get("success")
    footprint_ok = (
        footprint_created
        and footprint.get("validation", {}).get("success")
        and footprint.get("preview", {}).get("success")
    )
    remaining_review = footprint.get("validation_required", [])
    payload_gate = payload_validation.get("production_gate", {})
    payload_gate_reasons = list(payload_gate.get("reasons", []))
    generation_reasons: list[str] = []
    if not symbol_ok:
        generation_reasons.append("Symbol generation, file validation, or SVG preview export failed.")
    if not footprint_ok:
        generation_reasons.append("Footprint generation, file validation, or SVG preview export failed.")
    production_gate_reasons = payload_gate_reasons + generation_reasons

    return {
        "symbols_generated": symbol_ok,
        "footprint_generated": bool(footprint_created),
        "footprint_file_validates": bool(footprint_ok),
        "ready_for_production_without_review": symbol_ok and footprint_ok and not remaining_review,
        "remaining_review_items": remaining_review,
        "confidence_score_out_of_10": payload_validation.get("quality", {}).get(
            "score_out_of_10"
        ),
        "production_gate": {
            "pass": not production_gate_reasons,
            "reasons": production_gate_reasons,
            "payload_gate_pass": payload_gate.get("pass"),
            "generation_gate_pass": not generation_reasons,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute KiCad creator payloads.")
    parser.add_argument(
        "--payload",
        default="extracted/ACS37010_kicad_mcp_payloads.json",
        help="Resolver output JSON containing mcp_payloads.",
    )
    parser.add_argument(
        "--report",
        default="extracted/ACS37010_kicad_generation_report.json",
        help="Path to write execution/validation report.",
    )
    parser.add_argument(
        "--allow-draft-footprint",
        action="store_true",
        help="Generate footprint even when validation_required items remain.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = execute(Path(args.payload), Path(args.report), args.allow_draft_footprint)
    summary = report["validation_summary"]
    print(f"Wrote {args.report}")
    print(f"symbols_generated={summary['symbols_generated']}")
    print(f"footprint_generated={summary['footprint_generated']}")
    print(f"footprint_file_validates={summary['footprint_file_validates']}")
    print(
        "ready_for_production_without_review="
        f"{summary['ready_for_production_without_review']}"
    )
    print(f"confidence_score_out_of_10={summary.get('confidence_score_out_of_10')}")
    print(f"production_gate_pass={summary['production_gate']['pass']}")
    if summary["remaining_review_items"]:
        print("remaining_review_items:")
        for item in summary["remaining_review_items"]:
            print(f"  - {item['field']}: {item['reason']}")


if __name__ == "__main__":
    main()
