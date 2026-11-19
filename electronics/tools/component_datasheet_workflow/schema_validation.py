"""Validate normalized component JSON and its link to an acquired datasheet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).parent / "schema" / "normalized_component.schema.json"


def validate_component_schema(
    schema_path: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    schema_file = Path(schema_path).expanduser().resolve()
    if not schema_file.is_file():
        raise FileNotFoundError(f"normalized component JSON not found: {schema_file}")
    data = json.loads(schema_file.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: tuple(str(p) for p in e.absolute_path))
    problems = [
        {"path": ".".join(str(part) for part in error.absolute_path) or "$", "message": error.message}
        for error in errors
    ]

    if manifest_path and not errors:
        manifest_file = Path(manifest_path).expanduser().resolve()
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        expected = manifest["datasheet"]["sha256"].casefold()
        actual = data["datasheet"]["sha256"].casefold()
        if actual != expected:
            problems.append({"path": "datasheet.sha256", "message": "does not match the acquired datasheet manifest"})
        if data["component"]["manufacturer"].casefold() != manifest["component"]["manufacturer"].casefold():
            problems.append({"path": "component.manufacturer", "message": "does not match the acquired datasheet manifest"})
        if data["component"]["mpn"].casefold() != manifest["component"]["mpn"].casefold():
            problems.append({"path": "component.mpn", "message": "does not match the acquired datasheet manifest"})
        page_count = int(manifest["datasheet"]["page_count"])
        for path, evidence in _evidence_objects(data):
            for page in evidence.get("pages", []):
                if page > page_count:
                    problems.append({"path": f"{path}.pages", "message": f"page {page} exceeds the datasheet's {page_count} pages"})

    if isinstance(data.get("pins"), list) and isinstance(data.get("component"), dict):
        pin_numbers = [pin.get("number") for pin in data["pins"] if isinstance(pin, dict)]
        if all(isinstance(number, str) for number in pin_numbers) and len(pin_numbers) != len(set(pin_numbers)):
            problems.append({"path": "pins", "message": "physical pin numbers must be unique"})
        declared_count = data["component"].get("pin_count")
        if declared_count is not None and declared_count != len(pin_numbers):
            problems.append({"path": "component.pin_count", "message": f"declares {declared_count} pins but {len(pin_numbers)} pin records are present"})

    return {"valid": not problems, "schema_path": str(SCHEMA_PATH), "input_path": str(schema_file), "errors": problems}


def _evidence_objects(data: dict[str, Any]):
    component = data.get("component", {})
    if isinstance(component, dict):
        yield "component.evidence", component.get("evidence", {})
    for index, pin in enumerate(data.get("pins", [])):
        yield f"pins[{index}].evidence", pin.get("evidence", {})
    package = data.get("package", {})
    if isinstance(package, dict):
        yield "package.evidence", package.get("evidence", {})
    land_pattern = package.get("land_pattern") if isinstance(package, dict) else None
    if isinstance(land_pattern, dict):
        yield "package.land_pattern.evidence", land_pattern.get("evidence", {})
