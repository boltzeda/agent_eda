"""Generate KiCad libraries from this workflow's CAD-neutral component schema."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor"
PARSER_PLUGIN = VENDOR_ROOT / "kicad_parser"
RULE_RESOLVER = PARSER_PLUGIN / "kicad_rule_resolver.py"
PAYLOAD_EXECUTOR = PARSER_PLUGIN / "kicad_payload_executor.py"
REVIEW_REPORT = PARSER_PLUGIN / "generate_review_report.py"
PACKAGE_RULES = PARSER_PLUGIN / "kicad_rules" / "package_rules.json"
CREATOR_PYTHON = VENDOR_ROOT / "sym_and_fp_creator" / "python"


_PIN_TYPES = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "power_input": "power_in",
    "power_output": "power_out",
    "passive": "passive",
    "open_drain": "open_collector",
    "open_collector": "open_collector",
    "analog": "input",
    "clock": "input",
    "no_connect": "no_connect",
    "unspecified": "unspecified",
}
_PAD_SHAPES = {
    "rectangular": "rect",
    "rounded_rectangular": "roundrect",
    "circular": "circle",
    "oval": "oval",
}


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "component"


def _find_kicad_cli() -> Path | None:
    executable = "kicad-cli.exe" if sys.platform == "win32" else "kicad-cli"
    configured = os.environ.get("KICAD_CLI", "").strip().strip('"')
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured).expanduser()
        candidates.append(configured_path / executable if configured_path.is_dir() else configured_path)
    on_path = shutil.which(executable) or shutil.which("kicad-cli")
    if on_path:
        candidates.append(Path(on_path))

    roots: list[Path] = []
    root_setting = os.environ.get("KICAD_ROOT", "").strip().strip('"')
    if root_setting:
        roots.append(Path(root_setting).expanduser())
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            parent = os.environ.get(variable)
            if parent:
                roots.append(Path(parent) / ("Programs/KiCad" if variable == "LOCALAPPDATA" else "KiCad"))
        roots.append(Path("C:/KiCad"))
    elif sys.platform == "darwin":
        roots.extend((
            Path("/Applications/KiCad/KiCad.app/Contents"),
            Path("/Applications/KiCad.app/Contents"),
            Path("/opt/homebrew/opt/kicad"),
            Path("/usr/local/opt/kicad"),
        ))
    else:
        roots.extend((Path("/usr"), Path("/usr/local"), Path("/opt/kicad")))

    expanded = list(roots)
    for root in roots:
        if root.is_dir() and (sys.platform == "win32" or root == Path(root_setting or "!")):
            try:
                expanded.extend(path for path in root.iterdir() if path.is_dir())
            except OSError:
                pass
        if root.suffix.casefold() == ".app":
            expanded.append(root / "Contents")
    for root in expanded:
        candidates.extend((root / "bin" / executable, root / "MacOS" / executable, root / executable))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _dimension(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    try:
        return {"nominal": float(value)}
    except (TypeError, ValueError):
        return None


def _validate_component_input(schema_path: Path, manifest_path: str | Path | None) -> dict[str, Any]:
    try:
        data = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"component schema is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("component schema root must be a JSON object")
    identity = data.get("component")
    pins = data.get("pins")
    if not isinstance(identity, dict) or not identity.get("manufacturer") or not identity.get("mpn"):
        raise ValueError("schema requires component.manufacturer and component.mpn")
    if not isinstance(pins, list) or not pins:
        raise ValueError("schema requires a non-empty pins array")
    if identity.get("pin_count") is not None and identity["pin_count"] != len(pins):
        raise ValueError("component.pin_count does not match the number of pin records")
    for index, pin in enumerate(pins):
        if not isinstance(pin, dict) or not pin.get("number") or not pin.get("name"):
            raise ValueError(f"pins[{index}] requires number and name")

    if manifest_path:
        path = Path(manifest_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"datasheet manifest not found: {path}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("datasheet", {}).get("sha256", "").casefold() != data.get("datasheet", {}).get("sha256", "").casefold():
            raise ValueError("schema datasheet SHA-256 does not match the supplied manifest")
        manifest_identity = manifest.get("component", {})
        if str(manifest_identity.get("manufacturer", "")).casefold() != str(identity["manufacturer"]).casefold():
            raise ValueError("schema manufacturer does not match the supplied manifest")
        if str(manifest_identity.get("mpn", "")).casefold() != str(identity["mpn"]).casefold():
            raise ValueError("schema MPN does not match the supplied manifest")
    return data


def to_kicad_parser_schema(component: dict[str, Any], source_schema_path: Path) -> dict[str, Any]:
    """Adapt the CAD-neutral schema to the unchanged kicad_parser input shape."""
    identity = component.get("component") or {}
    datasheet = component.get("datasheet") or {}
    package = component.get("package") or {}
    pads_source = ((package.get("land_pattern") or {}).get("pads") or [])
    mpn = str(identity.get("mpn") or "").strip()
    manufacturer = str(identity.get("manufacturer") or "").strip()
    if not mpn or not manufacturer:
        raise ValueError("schema must contain component.manufacturer and component.mpn")

    pins = []
    for pin in component.get("pins") or []:
        electrical = str(pin.get("electrical_type") or "unspecified")
        mapped_type = _PIN_TYPES.get(electrical)
        if mapped_type is None:
            raise ValueError(f"unsupported electrical_type {electrical!r} on pin {pin.get('number')}")
        pins.append({
            "number": str(pin["number"]),
            "name": str(pin["name"]),
            "datasheet_description": str(pin.get("description") or ""),
            "suggested_kicad_type": mapped_type,
        })

    kicad_pads = []
    for pad in pads_source:
        shape = _PAD_SHAPES.get(str(pad.get("shape") or "").casefold())
        if shape is None:
            raise ValueError(
                f"pad {pad.get('number')} has unsupported shape {pad.get('shape')!r}; "
                "the schema-to-KiCad adapter will not approximate it"
            )
        drill = pad.get("drill_diameter_mm")
        plated = pad.get("plated")
        through_hole = drill is not None
        pad_type = "np_thru_hole" if through_hole and plated is False else ("thru_hole" if through_hole else "smd")
        layers = ["*.Cu", "*.Mask"] if through_hole else ["F.Cu", "F.Paste", "F.Mask"]
        converted = {
            "number": str(pad["number"]),
            "type": pad_type,
            "shape": shape,
            "at": {"x": float(pad["x_mm"]), "y": float(pad["y_mm"])} ,
            "size": {"w": float(pad["width_mm"]), "h": float(pad["height_mm"])},
            "layers": layers,
        }
        if drill is not None:
            converted["drill"] = float(drill)
        kicad_pads.append(converted)

    package_name = package.get("name") or package.get("package_code") or ""
    body_dimensions = package.get("body_dimensions_mm") or {}
    source_pdf = datasheet.get("source_url") or "~"
    return {
        "schema_version": 1,
        "source_pdf": source_pdf,
        "document": {
            "title": f"{manufacturer} {mpn} datasheet",
            "manufacturer": manufacturer,
            "part_number": mpn,
            "datasheet_revision": datasheet.get("revision") or "",
            "document_date": datasheet.get("document_date") or "",
        },
        "symbol_creation": {
            "base_part_family": str(identity.get("family") or mpn),
            "recommended_symbol_name": mpn,
            "reference_prefix": "U",
            "description": identity.get("description") or f"{manufacturer} {mpn}",
            "package_name": package_name,
            "package_suffix": str(package.get("package_code") or ""),
            "pin_count": int(identity.get("pin_count") or len(pins)),
            "pins": pins,
        },
        "footprint_creation": {
            "package": {
                "name": package_name,
                "suffix": str(package.get("package_code") or ""),
                "dimensions_unit": "mm",
                "body_length_x_mm": _dimension(body_dimensions.get("length")),
                "body_width_y_mm": _dimension(body_dimensions.get("width")),
                "body_height_z_mm": _dimension(body_dimensions.get("height")),
            },
            "kicad_pads": kicad_pads,
        },
        "source_component_schema": str(source_schema_path),
    }


def _run(command: list[str], *, cwd: Path, env: dict[str, str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True,
            timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"command timed out after {timeout_seconds}s: {command[1]}") from exc
    if result.returncode:
        detail = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-30:])
        raise RuntimeError(f"command failed ({result.returncode}): {command[1]}\n{detail}")
    return result


def generate_kicad_libraries(
    *,
    schema_path: str | Path,
    library_root: str | Path,
    manifest_path: str | Path | None = None,
    component_id: str | None = None,
    symbol_library_name: str | None = None,
    footprint_library_name: str | None = None,
    footprint_name: str | None = None,
    orientation: str | None = None,
    allow_draft_footprint: bool = False,
    overwrite: bool = False,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Generate libraries from the workflow schema via the existing resolver/creator scripts."""
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    schema_file = Path(schema_path).expanduser().resolve()
    if not schema_file.is_file():
        raise FileNotFoundError(f"component schema not found: {schema_file}")
    component = _validate_component_input(schema_file, manifest_path)
    converted = to_kicad_parser_schema(component, schema_file)
    exact_mpn = str((component.get("component") or {}).get("mpn") or "")
    stable_id = component_id or exact_mpn
    if not stable_id:
        raise ValueError("component MPN is missing; cannot name generated libraries")
    root = Path(library_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    work_dir = schema_file.parent / "kicad_library_generation"
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = _slug(stable_id)
    converted_path = work_dir / f"{stem}_kicad_parser_schema.json"
    payload_path = work_dir / f"{stem}_payloads.json"
    generation_report_path = work_dir / f"{stem}_generation_report.json"
    review_report_path = work_dir / f"{stem}_review_report.md"
    converted_path.write_text(json.dumps(converted, indent=2) + "\n", encoding="utf-8")

    required_paths = (RULE_RESOLVER, PAYLOAD_EXECUTOR, REVIEW_REPORT, PACKAGE_RULES, CREATOR_PYTHON)
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError("existing KiCad generation backend is incomplete: " + ", ".join(missing))
    kicad_cli = _find_kicad_cli()
    if kicad_cli is None:
        raise FileNotFoundError("kicad-cli is required for creator preview validation; set KICAD_CLI or add KiCad to PATH")

    resolver = [
        sys.executable, str(RULE_RESOLVER), "--input", str(converted_path),
        "--output", str(payload_path), "--library-root", str(root),
        "--package-rules", str(PACKAGE_RULES), "--package-rule-id", "auto",
        "--component-id", stable_id,
    ]
    for option, value in (
        ("--symbol-library-name", symbol_library_name),
        ("--footprint-library-name", footprint_library_name),
        ("--footprint-name", footprint_name),
        ("--orientation", orientation),
    ):
        if value:
            resolver.extend((option, value))
    if overwrite:
        resolver.append("--overwrite")

    env = os.environ.copy()
    creator_path = str(CREATOR_PYTHON)
    env["PYTHONPATH"] = creator_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PATH"] = str(kicad_cli.parent) + os.pathsep + env.get("PATH", "")
    _run(resolver, cwd=PARSER_PLUGIN, env=env, timeout_seconds=timeout_seconds)

    executor = [sys.executable, str(PAYLOAD_EXECUTOR), "--payload", str(payload_path), "--report", str(generation_report_path)]
    if allow_draft_footprint:
        executor.append("--allow-draft-footprint")
    _run(executor, cwd=PARSER_PLUGIN, env=env, timeout_seconds=timeout_seconds)

    review_command = [
        sys.executable, str(REVIEW_REPORT), "--payload", str(payload_path),
        "--generation-report", str(generation_report_path), "--output", str(review_report_path),
    ]
    _run(review_command, cwd=PARSER_PLUGIN, env=env, timeout_seconds=timeout_seconds)

    generation_report = json.loads(generation_report_path.read_text(encoding="utf-8"))
    summary = generation_report.get("validation_summary", {})
    payloads = json.loads(payload_path.read_text(encoding="utf-8"))
    libraries = payloads.get("mcp_payloads", {}).get("registration", {})
    generated_files = sorted(
        [str(path) for path in root.rglob("*.kicad_sym")]
        + [str(path) for path in root.rglob("*.kicad_mod")]
    )
    return {
        "success": bool(summary.get("symbols_generated"))
        and bool(summary.get("footprint_generated"))
        and bool(summary.get("footprint_file_validates")),
        "component": {"manufacturer": component["component"]["manufacturer"], "mpn": exact_mpn},
        "schema_path": str(schema_file),
        "converted_schema_path": str(converted_path),
        "library_root": str(root),
        "generated_files": generated_files,
        "symbol_library": libraries.get("symbol_library"),
        "footprint_library": libraries.get("footprint_library"),
        "payloads_path": str(payload_path),
        "generation_report_path": str(generation_report_path),
        "review_report_path": str(review_report_path),
        "validation_summary": summary,
        "kicad_parser_backend": "bundled resolver and sym_and_fp_creator under tools/kicad_library_generator/vendor",
    }
