"""CLI adapters for the standalone Agent-EDA component workflow packages."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.tool_environment import TOOLS_ROOT, isolated_tool_env, python_for


_TOOLS_ROOT = TOOLS_ROOT
_PREPARE_PYTHON = python_for("component_datasheet_prepare")
_PARSE_PYTHON = python_for("component_datasheet_parse")
_VALIDATE_PYTHON = python_for("component_schema_validate")
_FINDER_PYTHON = python_for("kicad_library_find")
_GENERATOR_PYTHON = python_for("kicad_library_generate")



def _run(module: str, args: list[str], *, cwd: Path, python: Path, timeout: int) -> str:
    if not cwd.is_dir():
        return json.dumps({"success": False, "error": f"Tool source directory is unavailable: {cwd}"})
    if not python.is_file():
        return json.dumps({"success": False, "error": f"Tool Python environment is unavailable: {python}"})
    # Ignore Python path/home overrides at interpreter startup as well as in env.
    # Keep cwd importable: these packages are vendored under tools/, not installed.
    try:
        result = subprocess.run(
            [str(python), "-E", "-s", "-m", module, *args], cwd=cwd, shell=False,
            capture_output=True, text=True, timeout=timeout, check=False, env=isolated_tool_env(),
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"success": False, "error": f"{module} exceeded its {timeout}-second limit."})
    except OSError as exc:
        return json.dumps({"success": False, "error": f"Could not start {module}: {exc}"})

    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"stdout": result.stdout[-24000:]}
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload.setdefault("exit_code", result.returncode)
    if result.returncode != 0:
        payload["success"] = False
    if result.stderr.strip():
        payload.setdefault("stderr", result.stderr[-12000:])
    return json.dumps(payload, ensure_ascii=False)


def component_datasheet_prepare(args: dict[str, Any], **_kwargs: Any) -> str:
    manufacturer = str(args.get("manufacturer", "")).strip()
    mpn = str(args.get("mpn", "")).strip()
    output_dir = str(args.get("output_dir", "")).strip()
    pdf_path = str(args.get("pdf_path", "")).strip()
    pdf_url = str(args.get("pdf_url", "")).strip()
    source_type = str(args.get("source_type", "")).strip()
    if not manufacturer or not mpn or not output_dir or bool(pdf_path) == bool(pdf_url):
        return json.dumps({"success": False, "error": "Provide manufacturer, mpn, output_dir, and exactly one of pdf_path or pdf_url."})
    argv = ["prepare", "--manufacturer", manufacturer, "--mpn", mpn, "--output-dir", output_dir]
    argv += ["--pdf-path", pdf_path] if pdf_path else ["--pdf-url", pdf_url]
    if source_type:
        argv += ["--source-type", source_type]
    return _run("component_datasheet_workflow", argv, cwd=_TOOLS_ROOT,
                python=_PREPARE_PYTHON, timeout=1800)


def component_datasheet_parse(args: dict[str, Any], **_kwargs: Any) -> str:
    manifest = str(args.get("manifest_path", "")).strip()
    if not manifest:
        return json.dumps({"success": False, "error": "manifest_path is required."})
    timeout = max(1, min(int(args.get("timeout_seconds", 1800)), 1800))
    argv = ["parse", "--manifest", manifest, "--backend", "pipeline", "--timeout-seconds", str(timeout)]
    if output_dir := str(args.get("output_dir", "")).strip():
        argv += ["--output-dir", output_dir]
    return _run("component_datasheet_workflow", argv, cwd=_TOOLS_ROOT,
                python=_PARSE_PYTHON, timeout=timeout + 60)


def component_schema_validate(args: dict[str, Any], **_kwargs: Any) -> str:
    schema = str(args.get("schema_json", "")).strip()
    if not schema:
        return json.dumps({"success": False, "error": "schema_json is required."})
    argv = ["validate", schema]
    if manifest := str(args.get("manifest_path", "")).strip():
        argv += ["--manifest", manifest]
    return _run("component_datasheet_workflow", argv, cwd=_TOOLS_ROOT,
                python=_VALIDATE_PYTHON, timeout=60)


def kicad_library_find(args: dict[str, Any], **_kwargs: Any) -> str:
    schema = str(args.get("schema_json", "")).strip()
    symbol = str(args.get("symbol_name", "")).strip()
    if bool(schema) == bool(symbol):
        return json.dumps({"success": False, "error": "Provide exactly one of schema_json or symbol_name."})
    argv = ["--schema-json", schema] if schema else ["--symbol-name", symbol]
    for key, flag in (("package_name", "--package-name"), ("footprint_name", "--footprint-name"),
                      ("symbols_dir", "--symbols-dir"), ("footprints_dir", "--footprints-dir")):
        if value := str(args.get(key, "")).strip():
            argv += [flag, value]
    argv += ["--max-footprint-matches", str(max(1, min(int(args.get("max_footprint_matches", 50)), 200)))]
    return _run("kicad_library_finder", argv, cwd=_TOOLS_ROOT,
                python=_FINDER_PYTHON, timeout=120)


def kicad_library_generate(args: dict[str, Any], **_kwargs: Any) -> str:
    schema = str(args.get("schema_json", "")).strip()
    library_root = str(args.get("library_root", "")).strip()
    if not schema or not library_root:
        return json.dumps({"success": False, "error": "schema_json and library_root are required."})
    argv = [schema, "--library-root", library_root]
    for key, flag in (("manifest_path", "--manifest"), ("component_id", "--component-id"),
                      ("symbol_library_name", "--symbol-library-name"),
                      ("footprint_library_name", "--footprint-library-name"),
                      ("footprint_name", "--footprint-name"), ("orientation", "--orientation")):
        if value := str(args.get(key, "")).strip():
            argv += [flag, value]
    if args.get("allow_draft_footprint") is True:
        argv.append("--allow-draft-footprint")
    if args.get("overwrite") is True:
        argv.append("--overwrite")
    timeout = max(1, min(int(args.get("timeout_seconds", 300)), 1800))
    argv += ["--timeout-seconds", str(timeout)]
    return _run("kicad_library_generator", argv, cwd=_TOOLS_ROOT,
                python=_GENERATOR_PYTHON, timeout=timeout + 30)


def _definition(name: str, description: str, properties: dict, required: list[str], handler,
                emoji: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "schema": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object", "properties": properties, "required": required,
                "additionalProperties": False,
            },
        },
        "handler": handler,
        "emoji": emoji,
    }


_STRING = {"type": "string"}
TOOL_DEFINITIONS = [
    _definition("component_datasheet_prepare", "Validate a user-provided or official component datasheet PDF and create a provenance manifest.", {
        "manufacturer": _STRING, "mpn": _STRING, "pdf_path": _STRING, "pdf_url": _STRING,
        "source_type": {"type": "string", "enum": ["manufacturer", "authorized_distributor_mirror", "user_provided"]},
        "output_dir": _STRING,
    }, ["manufacturer", "mpn", "output_dir"], component_datasheet_prepare, "📄"),
    _definition("component_datasheet_parse", "Parse a prepared datasheet using the installed MinerU pipeline and return paths to reviewable outputs.", {
        "manifest_path": _STRING, "output_dir": _STRING,
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800},
    }, ["manifest_path"], component_datasheet_parse, "🔎"),
    _definition("component_schema_validate", "Validate normalized component JSON against the project's schema and optional datasheet manifest.", {
        "schema_json": _STRING, "manifest_path": _STRING,
    }, ["schema_json"], component_schema_validate, "✅"),
    _definition("kicad_library_find", "Find exact symbol and candidate footprint matches in locally installed KiCad libraries.", {
        "schema_json": _STRING, "symbol_name": _STRING, "package_name": _STRING,
        "footprint_name": _STRING, "symbols_dir": _STRING, "footprints_dir": _STRING,
        "max_footprint_matches": {"type": "integer", "minimum": 1, "maximum": 200},
    }, [], kicad_library_find, "🔍"),
    _definition("kicad_library_generate", "Generate KiCad symbol and footprint libraries from reviewed normalized component JSON. Existing files are preserved unless overwrite is explicitly true.", {
        "schema_json": _STRING, "library_root": _STRING, "manifest_path": _STRING,
        "component_id": _STRING, "symbol_library_name": _STRING,
        "footprint_library_name": _STRING, "footprint_name": _STRING, "orientation": _STRING,
        "allow_draft_footprint": {"type": "boolean"}, "overwrite": {"type": "boolean"},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800},
    }, ["schema_json", "library_root"], kicad_library_generate, "🧩"),
]
