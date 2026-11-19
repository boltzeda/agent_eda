#!/usr/bin/env python3
"""Convert extracted datasheet facts into KiCad MCP creator payloads.

This script is the rule-based layer between MinerU extraction and the
KiCAD-MCP-Server create_symbol/create_footprint tools.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


STATUS_EXACT = "datasheet_exact"
STATUS_RULE = "derived_by_rule"
STATUS_VALIDATE = "needs_validation"
STATUS_USER = "user_config"
STATUS_REVIEWED = "review_confirmed"

STATUS_WEIGHTS = {
    STATUS_EXACT: 1.0,
    STATUS_REVIEWED: 1.0,
    STATUS_USER: 1.0,
    STATUS_RULE: 0.85,
    STATUS_VALIDATE: 0.25,
}

DEFAULT_PACKAGE_RULES = Path(__file__).resolve().parent / "kicad_rules" / "package_rules.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_optional_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return load_json(path)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def load_package_rules(path: Path) -> dict[str, Any]:
    data = load_json(path)
    rules = data.get("package_rules", {})
    if not isinstance(rules, dict):
        raise ValueError(f"Invalid package rule registry: {path}")
    return rules


def infer_reference_prefix(part_family: str) -> str:
    if any(token in part_family.lower() for token in ["sensor", "ic", "acs"]):
        return "U"
    return "U"


def slugify(value: str, fallback: str = "Component") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug or fallback


def component_label(extracted: dict[str, Any], override: str = "") -> str:
    if override:
        return override
    symbol_data = extracted.get("symbol_creation", {})
    document = extracted.get("document", {})
    return (
        symbol_data.get("base_part_family")
        or document.get("part_number")
        or document.get("title")
        or "Component"
    )


def display_name(value: str) -> str:
    return slugify(value).replace("_", " ")


def get_symbol_variants(symbol_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized symbol variants from generic or legacy ACS schemas."""
    generic = symbol_data.get("symbol_variants")
    if isinstance(generic, list) and generic:
        variants = []
        for index, variant in enumerate(generic):
            variant_id = str(variant.get("id") or variant.get("name") or f"variant_{index + 1}")
            variants.append(
                {
                    "id": variant_id,
                    "symbol_name": variant.get("recommended_symbol_name")
                    or variant.get("symbol_name")
                    or slugify(variant_id),
                    "pin_overrides": variant.get("pin_overrides", {}),
                }
            )
        return variants

    legacy = []
    for key, value in symbol_data.items():
        if key.endswith("_symbol_variant") and isinstance(value, dict):
            variant_id = key.removesuffix("_symbol_variant").upper()
            legacy.append(
                {
                    "id": variant_id,
                    "symbol_name": value.get("recommended_symbol_name") or slugify(variant_id),
                    "pin_overrides": {
                        key.removeprefix("pin_").removesuffix("_name"): {"name": item}
                        for key, item in value.items()
                        if key.startswith("pin_") and key.endswith("_name")
                    },
                }
            )
    if legacy:
        return legacy

    family = symbol_data.get("base_part_family") or "Component"
    return [
        {
            "id": str(family),
            "symbol_name": symbol_data.get("recommended_symbol_name") or slugify(str(family)),
            "pin_overrides": {},
        }
    ]


def pin_applies_to_variant(pin: dict[str, Any], variant_id: str) -> bool:
    applies_to = pin.get("applies_to")
    if not applies_to:
        return True
    if isinstance(applies_to, list):
        return variant_id in {str(item) for item in applies_to}
    return str(applies_to).upper() == variant_id.upper()


def variant_pin_name(pin: dict[str, Any], variant: dict[str, Any]) -> str:
    number = str(pin["number"])
    override = variant.get("pin_overrides", {}).get(number)
    if isinstance(override, dict) and override.get("name"):
        return str(override["name"])
    if isinstance(override, str):
        return override
    return str(pin["name"])


def pin_type(pin: dict[str, Any]) -> tuple[str, str]:
    """Return KiCad pin type and provenance status."""
    if "suggested_kicad_type" in pin:
        return pin["suggested_kicad_type"], STATUS_RULE

    name = str(pin.get("name", "")).upper()
    desc = str(pin.get("datasheet_description", "")).lower()

    if name in {"VDD", "VCC", "VIN", "VBAT", "GND", "VSS"}:
        return "power_in", STATUS_RULE
    if name.startswith("NC") or name == "N/C":
        return "no_connect", STATUS_RULE
    if "open-drain" in desc or "fault output" in desc:
        return "open_collector", STATUS_RULE
    if "output" in desc:
        return "output", STATUS_RULE
    if "input" in desc:
        return "input", STATUS_RULE
    return "passive", STATUS_RULE


def symbol_pin_positions(symbol_kind: str) -> dict[str, dict[str, float]]:
    """Provide deterministic schematic pin placement in mm.

    KiCad symbol coordinates use +Y upward. Pin endpoint coordinates are the
    wire connection points, following FOOTPRINT_SYMBOL_CREATOR_GUIDE.md.
    """
    pin4 = "VREF" if symbol_kind == "ACS37010" else "FAULT"
    return {
        "IP+": {"x": -7.62, "y": 2.54, "angle": 0},
        "IP-": {"x": -7.62, "y": 0.0, "angle": 0},
        "VDD": {"x": 7.62, "y": 5.08, "angle": 180},
        "VOUT": {"x": 7.62, "y": 2.54, "angle": 180},
        pin4: {"x": 7.62, "y": 0.0, "angle": 180},
        "GND": {"x": 7.62, "y": -2.54, "angle": 180},
    }


def infer_symbol_side(pin: dict[str, Any], kicad_type: str) -> str:
    if pin.get("symbol_side") in {"left", "right", "top", "bottom"}:
        return str(pin["symbol_side"])

    name = str(pin.get("name", "")).upper()
    if name in {"GND", "VSS", "AGND", "DGND", "PGND"}:
        return "bottom"
    if name in {"VDD", "VCC", "VIN", "VBAT", "AVDD", "DVDD", "PVDD"}:
        return "top"
    if kicad_type in {"output", "open_collector", "open_emitter", "power_out"}:
        return "right"
    if kicad_type in {"input", "tri_state"}:
        return "left"
    return "left"


def centered_axis(count: int, spacing: float = 2.54) -> list[float]:
    if count <= 0:
        return []
    start = ((count - 1) * spacing) / 2.0
    return [round(start - index * spacing, 3) for index in range(count)]


def symbol_layout_for_pins(
    pins: list[dict[str, Any]],
    body_half_width: float = 5.08,
    pin_length: float = 2.54,
) -> tuple[dict[int, dict[str, float]], dict[str, Any]]:
    """Create generic KiCad symbol pin coordinates from pin side groups."""
    grouped: dict[str, list[int]] = {"left": [], "right": [], "top": [], "bottom": []}
    for index, pin in enumerate(pins):
        grouped[infer_symbol_side(pin, str(pin["type"]))].append(index)

    max_vertical = max(len(grouped["left"]), len(grouped["right"]), 2)
    max_horizontal = max(len(grouped["top"]), len(grouped["bottom"]), 2)
    body_half_height = max(5.08, ((max_vertical - 1) * 2.54) / 2.0 + 2.54)
    body_half_width = max(body_half_width, ((max_horizontal - 1) * 2.54) / 2.0 + 2.54)

    positions: dict[int, dict[str, float]] = {}
    for side in ["left", "right"]:
        for pin_index, y in zip(grouped[side], centered_axis(len(grouped[side]))):
            x = -body_half_width - pin_length if side == "left" else body_half_width + pin_length
            positions[pin_index] = {"x": round(x, 3), "y": y, "angle": 0 if side == "left" else 180}

    for side in ["top", "bottom"]:
        for pin_index, x in zip(grouped[side], centered_axis(len(grouped[side]))):
            y = body_half_height + pin_length if side == "top" else -body_half_height - pin_length
            positions[pin_index] = {"x": x, "y": round(y, 3), "angle": 270 if side == "top" else 90}

    rectangle = {
        "x1": round(-body_half_width, 3),
        "y1": round(body_half_height, 3),
        "x2": round(body_half_width, 3),
        "y2": round(-body_half_height, 3),
        "width": 0.254,
        "fill": "background",
    }
    return positions, rectangle


def build_symbol_payload(
    extracted: dict[str, Any],
    symbol_kind: str,
    library_path: Path,
    footprint_ref: str,
    overwrite: bool,
) -> dict[str, Any]:
    symbol_data = extracted["symbol_creation"]
    pin_positions = symbol_pin_positions(symbol_kind)
    pins: list[dict[str, Any]] = []
    field_status: dict[str, str] = {}

    for pin in symbol_data["pins"]:
        applies_to = pin.get("applies_to")
        if applies_to and applies_to != symbol_kind:
            continue

        name = pin["name"]
        number = str(pin["number"])
        kicad_type, type_status = pin_type(pin)
        at = pin_positions[name]
        pins.append(
            {
                "name": name,
                "number": number,
                "type": kicad_type,
                "at": at,
                "length": 2.54,
                "shape": "line",
            }
        )
        field_status[f"pins.{number}.name"] = STATUS_EXACT
        field_status[f"pins.{number}.number"] = STATUS_EXACT
        field_status[f"pins.{number}.type"] = type_status
        field_status[f"pins.{number}.at"] = STATUS_RULE
        field_status[f"pins.{number}.length"] = STATUS_RULE

    name = symbol_data[f"{symbol_kind.lower()}_symbol_variant"]["recommended_symbol_name"]
    payload = {
        "libraryPath": str(library_path),
        "name": name,
        "referencePrefix": infer_reference_prefix(symbol_data["base_part_family"]),
        "description": (
            f"{symbol_kind} high accuracy current sensor, "
            f"{symbol_data['package_name']} package"
        ),
        "keywords": "Allegro current sensor SOIC-6 LZ",
        "datasheet": extracted["source_pdf"],
        "footprint": footprint_ref,
        "inBom": True,
        "onBoard": True,
        "pins": pins,
        "rectangles": [
            {
                "x1": -5.08,
                "y1": 6.35,
                "x2": 5.08,
                "y2": -5.08,
                "width": 0.254,
                "fill": "background",
            }
        ],
        "overwrite": overwrite,
    }

    field_status.update(
        {
            "libraryPath": STATUS_USER,
            "name": STATUS_RULE,
            "referencePrefix": STATUS_RULE,
            "description": STATUS_RULE,
            "keywords": STATUS_RULE,
            "datasheet": STATUS_EXACT,
            "footprint": STATUS_RULE,
            "inBom": STATUS_RULE,
            "onBoard": STATUS_RULE,
            "rectangles": STATUS_RULE,
            "overwrite": STATUS_USER,
        }
    )
    return {"payload": payload, "field_status": field_status}


def build_symbol_payload_generic(
    extracted: dict[str, Any],
    variant: dict[str, Any],
    library_path: Path,
    footprint_ref: str,
    overwrite: bool,
) -> dict[str, Any]:
    symbol_data = extracted["symbol_creation"]
    pins: list[dict[str, Any]] = []
    field_status: dict[str, str] = {}
    variant_id = str(variant["id"])

    for pin in symbol_data["pins"]:
        if not pin_applies_to_variant(pin, variant_id):
            continue

        name = variant_pin_name(pin, variant)
        number = str(pin["number"])
        kicad_type, type_status = pin_type({**pin, "name": name})
        pins.append(
            {
                "name": name,
                "number": number,
                "type": kicad_type,
                "length": float(pin.get("length_mm", 2.54)),
                "shape": pin.get("shape", "line"),
                "symbol_side": pin.get("symbol_side"),
            }
        )
        field_status[f"pins.{number}.name"] = STATUS_EXACT
        field_status[f"pins.{number}.number"] = STATUS_EXACT
        field_status[f"pins.{number}.type"] = type_status
        field_status[f"pins.{number}.at"] = STATUS_RULE
        field_status[f"pins.{number}.length"] = STATUS_RULE

    positions, rectangle = symbol_layout_for_pins(pins)
    for index, pin in enumerate(pins):
        pin["at"] = positions[index]
        pin.pop("symbol_side", None)

    family = str(symbol_data.get("base_part_family") or variant_id)
    package_name = symbol_data.get("package_name") or extracted.get("footprint_creation", {}).get(
        "package", {}
    ).get("name", "")
    payload = {
        "libraryPath": str(library_path),
        "name": variant["symbol_name"],
        "referencePrefix": symbol_data.get("reference_prefix")
        or infer_reference_prefix(family),
        "description": symbol_data.get("description")
        or f"{display_name(variant_id)} {package_name}".strip(),
        "keywords": symbol_data.get("keywords")
        or f"{family} {package_name}".strip(),
        "datasheet": extracted.get("source_pdf", "~"),
        "footprint": footprint_ref,
        "inBom": symbol_data.get("in_bom", True),
        "onBoard": symbol_data.get("on_board", True),
        "pins": pins,
        "rectangles": symbol_data.get("rectangles", [rectangle]),
        "overwrite": overwrite,
    }

    field_status.update(
        {
            "libraryPath": STATUS_USER,
            "name": STATUS_RULE,
            "referencePrefix": STATUS_RULE,
            "description": STATUS_RULE,
            "keywords": STATUS_RULE,
            "datasheet": STATUS_EXACT if extracted.get("source_pdf") else STATUS_USER,
            "footprint": STATUS_RULE,
            "inBom": STATUS_RULE,
            "onBoard": STATUS_RULE,
            "rectangles": STATUS_RULE,
            "overwrite": STATUS_USER,
        }
    )
    return {"payload": payload, "field_status": field_status}


def build_footprint_payload(
    extracted: dict[str, Any],
    library_path: Path,
    name: str,
    orientation: str,
    overwrite: bool,
    review: dict[str, Any],
    package_rule: dict[str, Any],
) -> dict[str, Any]:
    fp = extracted["footprint_creation"]
    pads = footprint_pads(fp, orientation)
    review_result = review_confirms_candidate_pad_mapping(review, orientation, pads)
    courtyard = fp["derived_or_recommended_kicad_footprint_fields"]["courtyard"][
        "derived_extents_mm"
    ]
    fab = fp["derived_or_recommended_kicad_footprint_fields"]["fabrication_outline"][
        "body_outline_mm"
    ]

    payload = {
        "libraryPath": str(library_path),
        "name": name,
        "description": "Allegro ACS37010/ACS37012 custom 6-pin SOIC LZ footprint",
        "tags": "Allegro ACS37010 ACS37012 current sensor SOIC-6 LZ",
        "pads": pads,
        "courtyard": {
            "x1": courtyard["x_min"],
            "y1": courtyard["y_min"],
            "x2": courtyard["x_max"],
            "y2": courtyard["y_max"],
            "width": 0.05,
        },
        "fabLayer": {
            "x1": fab["x_min"],
            "y1": fab["y_min"],
            "x2": fab["x_max"],
            "y2": fab["y_max"],
            "width": 0.1,
        },
        "silkscreen": {
            "x1": -2.7,
            "y1": -2.2,
            "x2": 2.7,
            "y2": 2.2,
            "width": 0.12,
        },
        "refPosition": {"x": 0.0, "y": -4.0},
        "valuePosition": {"x": 0.0, "y": 4.0},
        "overwrite": overwrite,
    }

    validation_required = []
    pad_number_status = STATUS_REVIEWED if review_result["confirmed"] else STATUS_VALIDATE
    if not review_result["confirmed"]:
        validation_required.append(
            {
                "field": "pads[].number",
                "reason": (
                    "The ACS37010 datasheet gives the land-pattern geometry, "
                    "but the PCB layout reference image does not print pad numbers."
                ),
            }
        )
    validation_required.extend(review_result["errors"])

    field_status = {
        "libraryPath": STATUS_USER,
        "name": STATUS_RULE,
        "description": STATUS_RULE,
        "tags": STATUS_RULE,
        "pads[].number": pad_number_status,
        "pads[].type": STATUS_RULE,
        "pads[].shape": STATUS_RULE,
        "pads[].at": STATUS_RULE,
        "pads[].size": STATUS_EXACT,
        "pads[].layers": STATUS_RULE,
        "courtyard": STATUS_RULE,
        "fabLayer": STATUS_RULE,
        "silkscreen": STATUS_RULE,
        "refPosition": STATUS_RULE,
        "valuePosition": STATUS_RULE,
        "overwrite": STATUS_USER,
    }

    return {
        "payload": payload,
        "field_status": field_status,
        "package_rule": package_rule,
        "review_status": review_result,
        "ready_for_unreviewed_production": False,
        "validation_required": validation_required,
    }


def footprint_pads(fp: dict[str, Any], orientation: str) -> list[dict[str, Any]]:
    """Return candidate MCP pad list for the ACS37010 LZ package.

    Footprint coordinates follow KiCad/MCP convention from the guide: +Y down.
    The selected numbering is a candidate and must be reviewed.
    """
    layout = fp["pcb_layout_reference"]
    ip_size = layout["primary_current_pad"]
    signal_size = layout["signal_pad"]
    layers = fp["derived_or_recommended_kicad_footprint_fields"]["pad_layers"]

    signal_x = [-1.905, -0.635, 0.635, 1.905]
    ip_x = [-1.27, 1.27]
    row_y = 2.1

    if orientation != "branding_reference_as_drawn":
        raise ValueError(
            "Only 'branding_reference_as_drawn' is implemented for the ACS37010 draft footprint"
        )

    raw_pads = [
        ("3", signal_x[0], -row_y, signal_size),
        ("4", signal_x[1], -row_y, signal_size),
        ("5", signal_x[2], -row_y, signal_size),
        ("6", signal_x[3], -row_y, signal_size),
        ("1", ip_x[0], row_y, ip_size),
        ("2", ip_x[1], row_y, ip_size),
    ]

    pads = []
    for number, x, y, size in raw_pads:
        pads.append(
            {
                "number": number,
                "type": "smd",
                "shape": "rect",
                "at": {"x": x, "y": y},
                "size": {"w": size["size_x_mm"], "h": size["size_y_mm"]},
                "layers": layers,
            }
        )
    return pads


def review_confirms_candidate_pad_mapping(
    review: dict[str, Any],
    orientation: str,
    pads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check whether a human/official-source review clears pad numbering risk."""
    if not review:
        return {
            "review_file_loaded": False,
            "confirmed": False,
            "errors": [],
            "evidence": [],
        }

    footprint = review.get("footprint", {})
    confirmations = review.get("confirmations", {})
    orientation_confirmed = bool(
        footprint.get("orientation_confirmed")
        or confirmations.get("footprint.orientation") == "confirmed"
    )
    pad_numbering_confirmed = bool(
        footprint.get("pad_numbering_confirmed")
        or confirmations.get("footprint.pad_numbering") == "confirmed"
    )

    errors: list[dict[str, str]] = []
    review_orientation = footprint.get("orientation")
    if review_orientation and review_orientation != orientation:
        errors.append(
            {
                "field": "footprint.orientation",
                "reason": (
                    f"Review file confirms orientation '{review_orientation}', "
                    f"but resolver is using '{orientation}'."
                ),
            }
        )

    mapping = footprint.get("pad_number_to_coordinate_mapping", {})
    if mapping:
        errors.extend(compare_review_mapping(mapping, pads))

    confirmed = bool(
        orientation_confirmed
        and pad_numbering_confirmed
        and not errors
        and review.get("review_status", "draft") == "confirmed"
    )
    return {
        "review_file_loaded": True,
        "confirmed": confirmed,
        "errors": errors,
        "evidence": review.get("evidence", []),
        "review_status": review.get("review_status", "draft"),
        "orientation_confirmed": orientation_confirmed,
        "pad_numbering_confirmed": pad_numbering_confirmed,
    }


def compare_review_mapping(
    mapping: dict[str, Any],
    pads: list[dict[str, Any]],
    tolerance_mm: float = 0.01,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    candidates = {str(pad["number"]): pad for pad in pads}
    for number, expected in mapping.items():
        pad = candidates.get(str(number))
        if pad is None:
            errors.append(
                {
                    "field": f"pads.{number}.number",
                    "reason": "Review file names a pad number that is not in the generated footprint.",
                }
            )
            continue
        for axis in ["x", "y"]:
            actual_value = float(pad["at"][axis])
            expected_value = float(expected[axis])
            if abs(actual_value - expected_value) > tolerance_mm:
                errors.append(
                    {
                        "field": f"pads.{number}.at.{axis}",
                        "reason": (
                            f"Review expected {expected_value} mm, "
                            f"resolver generated {actual_value} mm."
                        ),
                    }
                )
    return errors


def nominal(value: Any, default: float = 0.0) -> float:
    if isinstance(value, dict):
        return float(value.get("nominal", default))
    if value in (None, ""):
        return default
    return float(value)


def explicit_pads_from_schema(fp: dict[str, Any]) -> list[dict[str, Any]]:
    pads = (
        fp.get("kicad_pads")
        or fp.get("pads")
        or fp.get("kicad_footprint", {}).get("pads")
        or fp.get("footprint_payload", {}).get("pads")
        or []
    )
    normalized = []
    for pad in pads:
        size = pad.get("size", {})
        at = pad.get("at", {})
        normalized.append(
            {
                "number": str(pad["number"]),
                "type": pad.get("type", "smd"),
                "shape": pad.get("shape", "rect"),
                "at": {
                    "x": float(at.get("x", pad.get("x", 0.0))),
                    "y": float(at.get("y", pad.get("y", 0.0))),
                    **({"angle": float(at["angle"])} if "angle" in at else {}),
                },
                "size": {
                    "w": float(size.get("w", pad.get("size_x_mm", pad.get("w", 0.0)))),
                    "h": float(size.get("h", pad.get("size_y_mm", pad.get("h", 0.0)))),
                },
                "layers": pad.get("layers", ["F.Cu", "F.Paste", "F.Mask"]),
                **({"drill": pad["drill"]} if "drill" in pad else {}),
            }
        )
    return normalized


def pads_bounds(pads: list[dict[str, Any]]) -> dict[str, float]:
    if not pads:
        return {"x_min": -1.0, "x_max": 1.0, "y_min": -1.0, "y_max": 1.0}
    x_min = min(float(pad["at"]["x"]) - float(pad["size"]["w"]) / 2.0 for pad in pads)
    x_max = max(float(pad["at"]["x"]) + float(pad["size"]["w"]) / 2.0 for pad in pads)
    y_min = min(float(pad["at"]["y"]) - float(pad["size"]["h"]) / 2.0 for pad in pads)
    y_max = max(float(pad["at"]["y"]) + float(pad["size"]["h"]) / 2.0 for pad in pads)
    return {
        "x_min": round(x_min, 3),
        "x_max": round(x_max, 3),
        "y_min": round(y_min, 3),
        "y_max": round(y_max, 3),
    }


def rectangle_from_bounds(bounds: dict[str, float], clearance: float, width: float) -> dict[str, float]:
    return {
        "x1": round(bounds["x_min"] - clearance, 3),
        "y1": round(bounds["y_min"] - clearance, 3),
        "x2": round(bounds["x_max"] + clearance, 3),
        "y2": round(bounds["y_max"] + clearance, 3),
        "width": width,
    }


def fab_outline_from_package(fp: dict[str, Any], pads: list[dict[str, Any]]) -> dict[str, float]:
    package = fp.get("package", {})
    length = nominal(package.get("body_length_x_mm"))
    width = nominal(package.get("body_width_y_mm"))
    if length and width:
        return {
            "x1": round(-length / 2.0, 3),
            "y1": round(-width / 2.0, 3),
            "x2": round(length / 2.0, 3),
            "y2": round(width / 2.0, 3),
            "width": 0.1,
        }
    return rectangle_from_bounds(pads_bounds(pads), 0.0, 0.1)


def generated_dual_row_pads(
    extracted: dict[str, Any],
    package_rule: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    symbol_data = extracted.get("symbol_creation", {})
    fp = extracted.get("footprint_creation", {})
    layout = fp.get("pcb_layout_reference", {})
    lead_geometry = fp.get("lead_geometry", {})
    defaults = package_rule.get("footprint_rule", {})
    pin_count = int(symbol_data.get("pin_count") or len({p.get("number") for p in symbol_data.get("pins", [])}))
    if pin_count <= 0 or pin_count % 2:
        return [], [
            {
                "field": "symbol_creation.pin_count",
                "reason": "Generic dual-row SMD generation requires a positive even pin count.",
            }
        ]

    pad_shape = defaults.get("pad_shape", "rect")
    pad_layers = fp.get("derived_or_recommended_kicad_footprint_fields", {}).get(
        "pad_layers", ["F.Cu", "F.Paste", "F.Mask"]
    )
    pad_source = layout.get("signal_pad") or layout.get("pad") or {}
    pitch = float(
        pad_source.get("center_to_center_pitch_x_mm")
        or lead_geometry.get("signal_lead_pitch_mm")
        or defaults.get("pitch_mm", 1.27)
    )
    row_spacing = float(
        layout.get("row_center_to_center_y_mm")
        or defaults.get("row_center_to_center_y_mm", 0.0)
    )
    if not row_spacing:
        return [], [
            {
                "field": "footprint_creation.pcb_layout_reference.row_center_to_center_y_mm",
                "reason": "Generic dual-row SMD generation needs row center spacing.",
            }
        ]

    pad_w = float(pad_source.get("size_x_mm") or defaults.get("pad_size_x_mm", 0.65))
    pad_h = float(pad_source.get("size_y_mm") or defaults.get("pad_size_y_mm", 1.5))
    half = pin_count // 2
    xs = [round((index - (half - 1) / 2.0) * pitch, 3) for index in range(half)]
    top_y = round(-row_spacing / 2.0, 3)
    bottom_y = round(row_spacing / 2.0, 3)

    raw = []
    for offset, x in enumerate(xs):
        raw.append((str(offset + 1), x, top_y))
    for offset, x in enumerate(reversed(xs)):
        raw.append((str(half + offset + 1), x, bottom_y))

    pads = [
        {
            "number": number,
            "type": "smd",
            "shape": pad_shape,
            "at": {"x": x, "y": y},
            "size": {"w": pad_w, "h": pad_h},
            "layers": pad_layers,
        }
        for number, x, y in raw
    ]
    return pads, []


def generated_sot_pads(
    extracted: dict[str, Any],
    package_rule: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    symbol_data = extracted.get("symbol_creation", {})
    fp = extracted.get("footprint_creation", {})
    layout = fp.get("pcb_layout_reference", {})
    defaults = package_rule.get("footprint_rule", {})
    pin_count = int(symbol_data.get("pin_count") or len({p.get("number") for p in symbol_data.get("pins", [])}))
    if pin_count not in {3, 5, 6}:
        return [], [
            {
                "field": "symbol_creation.pin_count",
                "reason": "Generic SOT generator supports 3, 5, or 6 pins.",
            }
        ]

    pad_source = layout.get("pad") or layout.get("signal_pad") or {}
    pad_w = float(pad_source.get("size_x_mm") or defaults.get("pad_size_x_mm", 0.7))
    pad_h = float(pad_source.get("size_y_mm") or defaults.get("pad_size_y_mm", 0.95))
    pitch = float(
        pad_source.get("center_to_center_pitch_x_mm") or defaults.get("pitch_mm", 0.95)
    )
    row_spacing = float(
        layout.get("row_center_to_center_y_mm") or defaults.get("row_center_to_center_y_mm", 1.9)
    )
    layers = fp.get("derived_or_recommended_kicad_footprint_fields", {}).get(
        "pad_layers", ["F.Cu", "F.Paste", "F.Mask"]
    )

    if pin_count == 3:
        raw = [
            ("1", -pitch / 2.0, row_spacing / 2.0),
            ("2", pitch / 2.0, row_spacing / 2.0),
            ("3", 0.0, -row_spacing / 2.0),
        ]
    else:
        per_side = pin_count // 2
        xs = [round((index - (per_side - 1) / 2.0) * pitch, 3) for index in range(per_side)]
        raw = [(str(index + 1), x, row_spacing / 2.0) for index, x in enumerate(xs)]
        raw.extend(
            (str(per_side + index + 1), x, -row_spacing / 2.0)
            for index, x in enumerate(reversed(xs))
        )

    return [
        {
            "number": number,
            "type": "smd",
            "shape": defaults.get("pad_shape", "rect"),
            "at": {"x": round(x, 3), "y": round(y, 3)},
            "size": {"w": pad_w, "h": pad_h},
            "layers": layers,
        }
        for number, x, y in raw
    ], []


def generated_qfn_pads(
    extracted: dict[str, Any],
    package_rule: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    symbol_data = extracted.get("symbol_creation", {})
    fp = extracted.get("footprint_creation", {})
    package = fp.get("package", {})
    layout = fp.get("pcb_layout_reference", {})
    defaults = package_rule.get("footprint_rule", {})
    pin_count = int(symbol_data.get("pin_count") or len({p.get("number") for p in symbol_data.get("pins", [])}))
    if pin_count <= 0 or pin_count % 4:
        return [], [
            {
                "field": "symbol_creation.pin_count",
                "reason": "Generic QFN/DFN quad generator requires a pin count divisible by 4.",
            }
        ]

    side_count = pin_count // 4
    pad_source = layout.get("pad") or layout.get("signal_pad") or {}
    pitch = float(
        pad_source.get("center_to_center_pitch_x_mm") or defaults.get("pitch_mm", 0.5)
    )
    pad_w = float(pad_source.get("size_x_mm") or defaults.get("pad_size_x_mm", 0.28))
    pad_h = float(pad_source.get("size_y_mm") or defaults.get("pad_size_y_mm", 0.75))
    body_x = nominal(package.get("body_length_x_mm"), float(defaults.get("body_x_mm", 4.0)))
    body_y = nominal(package.get("body_width_y_mm"), float(defaults.get("body_y_mm", body_x)))
    row_x = float(layout.get("row_center_to_center_x_mm") or body_x + pad_h)
    row_y = float(layout.get("row_center_to_center_y_mm") or body_y + pad_h)
    layers = fp.get("derived_or_recommended_kicad_footprint_fields", {}).get(
        "pad_layers", ["F.Cu", "F.Paste", "F.Mask"]
    )

    axis = [round((index - (side_count - 1) / 2.0) * pitch, 3) for index in range(side_count)]
    raw: list[tuple[str, float, float, int]] = []
    number = 1
    for x in axis:
        raw.append((str(number), x, -row_y / 2.0, 0))
        number += 1
    for y in axis:
        raw.append((str(number), row_x / 2.0, y, 90))
        number += 1
    for x in reversed(axis):
        raw.append((str(number), x, row_y / 2.0, 0))
        number += 1
    for y in reversed(axis):
        raw.append((str(number), -row_x / 2.0, y, 90))
        number += 1

    pads = [
        {
            "number": pad_number,
            "type": "smd",
            "shape": defaults.get("pad_shape", "rect"),
            "at": {"x": round(x, 3), "y": round(y, 3), "angle": angle},
            "size": {"w": pad_w, "h": pad_h},
            "layers": layers,
        }
        for pad_number, x, y, angle in raw
    ]

    epad = layout.get("exposed_pad") or fp.get("exposed_pad")
    if epad:
        pads.append(
            {
                "number": str(epad.get("number", "EP")),
                "type": "smd",
                "shape": epad.get("shape", "rect"),
                "at": {"x": 0.0, "y": 0.0},
                "size": {
                    "w": float(epad.get("size_x_mm", epad.get("w", body_x * 0.55))),
                    "h": float(epad.get("size_y_mm", epad.get("h", body_y * 0.55))),
                },
                "layers": layers,
            }
        )

    return pads, []


def build_footprint_payload_generic(
    extracted: dict[str, Any],
    library_path: Path,
    name: str,
    overwrite: bool,
    review: dict[str, Any],
    package_rule: dict[str, Any],
    orientation: str,
) -> dict[str, Any]:
    fp = extracted.get("footprint_creation", {})
    rule = package_rule.get("footprint_rule", {})
    generator = rule.get("generator", "explicit_pads")
    validation_required: list[dict[str, str]] = []
    pads = explicit_pads_from_schema(fp)
    pad_number_status = STATUS_EXACT

    if not pads and generator in {"dual_row_smd", "sot_smd", "qfn_quad_smd"}:
        if generator == "sot_smd":
            pads, generation_errors = generated_sot_pads(extracted, package_rule)
        elif generator == "qfn_quad_smd":
            pads, generation_errors = generated_qfn_pads(extracted, package_rule)
        else:
            pads, generation_errors = generated_dual_row_pads(extracted, package_rule)
        validation_required.extend(generation_errors)
        review_result = review_confirms_candidate_pad_mapping(review, orientation, pads)
        pad_number_status = STATUS_REVIEWED if review_result["confirmed"] else STATUS_VALIDATE
        if not review_result["confirmed"]:
            validation_required.append(
                {
                    "field": "pads[].number",
                    "reason": (
                        f"Generic {generator} pad numbering depends on package orientation "
                        "and must be confirmed against the datasheet pin-1 view or an official footprint."
                    ),
                }
            )
        validation_required.extend(review_result["errors"])
    else:
        review_result = {
            "review_file_loaded": bool(review),
            "confirmed": bool(pads),
            "errors": [],
            "evidence": review.get("evidence", []) if review else [],
        }

    if not pads:
        validation_required.append(
            {
                "field": "pads",
                "reason": (
                    "No explicit pad list was found and the selected package rule "
                    "could not derive a footprint."
                ),
            }
        )

    derived = fp.get("derived_or_recommended_kicad_footprint_fields", {})
    bounds = pads_bounds(pads)
    courtyard_source = derived.get("courtyard", {}).get("derived_extents_mm")
    fab_source = derived.get("fabrication_outline", {}).get("body_outline_mm")
    if courtyard_source:
        courtyard = {
            "x1": courtyard_source["x_min"],
            "y1": courtyard_source["y_min"],
            "x2": courtyard_source["x_max"],
            "y2": courtyard_source["y_max"],
            "width": 0.05,
        }
    else:
        courtyard = rectangle_from_bounds(bounds, float(rule.get("courtyard_clearance_mm", 0.25)), 0.05)

    if fab_source:
        fab = {
            "x1": fab_source["x_min"],
            "y1": fab_source["y_min"],
            "x2": fab_source["x_max"],
            "y2": fab_source["y_max"],
            "width": 0.1,
        }
    else:
        fab = fab_outline_from_package(fp, pads)

    silkscreen = rectangle_from_bounds(pads_bounds([{"at": {"x": 0, "y": 0}, "size": {"w": abs(fab["x2"] - fab["x1"]), "h": abs(fab["y2"] - fab["y1"])}}]), 0.15, 0.12)
    package_name = fp.get("package", {}).get("name", "")
    payload = {
        "libraryPath": str(library_path),
        "name": name,
        "description": fp.get("description") or f"{package_name} generated footprint".strip(),
        "tags": fp.get("tags") or package_name,
        "pads": pads,
        "courtyard": courtyard,
        "fabLayer": fab,
        "silkscreen": silkscreen,
        "refPosition": {"x": 0.0, "y": round(courtyard["y1"] - 1.0, 3)},
        "valuePosition": {"x": 0.0, "y": round(courtyard["y2"] + 1.0, 3)},
        "overwrite": overwrite,
    }

    return {
        "payload": payload,
        "field_status": {
            "libraryPath": STATUS_USER,
            "name": STATUS_RULE,
            "description": STATUS_RULE,
            "tags": STATUS_RULE,
            "pads[].number": pad_number_status,
            "pads[].type": STATUS_RULE,
            "pads[].shape": STATUS_RULE,
            "pads[].at": STATUS_EXACT if generator == "explicit_pads" else STATUS_RULE,
            "pads[].size": STATUS_EXACT if generator == "explicit_pads" else STATUS_RULE,
            "pads[].layers": STATUS_RULE,
            "courtyard": STATUS_RULE,
            "fabLayer": STATUS_RULE,
            "silkscreen": STATUS_RULE,
            "refPosition": STATUS_RULE,
            "valuePosition": STATUS_RULE,
            "overwrite": STATUS_USER,
        },
        "package_rule": package_rule,
        "review_status": review_result,
        "ready_for_unreviewed_production": not validation_required,
        "validation_required": validation_required,
    }


def score_field_statuses(result: dict[str, Any]) -> dict[str, Any]:
    counts = {status: 0 for status in STATUS_WEIGHTS}
    weighted_total = 0.0
    field_total = 0

    for symbol in result["mcp_payloads"]["symbols"].values():
        for status in symbol["field_status"].values():
            counts[status] = counts.get(status, 0) + 1
            weighted_total += STATUS_WEIGHTS.get(status, 0.5)
            field_total += 1

    for status in result["mcp_payloads"]["footprint"]["field_status"].values():
        counts[status] = counts.get(status, 0) + 1
        weighted_total += STATUS_WEIGHTS.get(status, 0.5)
        field_total += 1

    score = 0.0 if field_total == 0 else round((weighted_total / field_total) * 10.0, 1)
    return {
        "score_out_of_10": score,
        "field_count": field_total,
        "status_counts": counts,
        "weights": STATUS_WEIGHTS,
    }


def build_validation_summary(result: dict[str, Any]) -> dict[str, Any]:
    missing_required: list[dict[str, str]] = []
    needs_validation: list[dict[str, str]] = []

    for symbol_name, symbol in result["mcp_payloads"]["symbols"].items():
        statuses = symbol["field_status"]
        for key in ["libraryPath", "name"]:
            if key not in symbol["payload"] or not symbol["payload"][key]:
                missing_required.append(
                    {"tool": "create_symbol", "symbol": symbol_name, "field": key}
                )
        for index, pin in enumerate(symbol["payload"].get("pins", [])):
            for key in ["name", "number", "type", "at"]:
                if key not in pin or pin[key] in ("", None):
                    missing_required.append(
                        {
                            "tool": "create_symbol",
                            "symbol": symbol_name,
                            "field": f"pins[{index}].{key}",
                        }
                    )
        for field, status in statuses.items():
            if status == STATUS_VALIDATE:
                needs_validation.append(
                    {"tool": "create_symbol", "symbol": symbol_name, "field": field}
                )

    footprint = result["mcp_payloads"]["footprint"]
    for key in ["libraryPath", "name"]:
        if key not in footprint["payload"] or not footprint["payload"][key]:
            missing_required.append({"tool": "create_footprint", "field": key})
    for index, pad in enumerate(footprint["payload"].get("pads", [])):
        for key in ["number", "type", "at", "size"]:
            if key not in pad or pad[key] in ("", None):
                missing_required.append(
                    {"tool": "create_footprint", "field": f"pads[{index}].{key}"}
                )
    for item in footprint.get("validation_required", []):
        needs_validation.append({"tool": "create_footprint", **item})

    quality = score_field_statuses(result)
    production_reasons: list[str] = []
    if missing_required:
        production_reasons.append("Required KiCad MCP fields are missing.")
    if needs_validation:
        production_reasons.append("One or more fields still need human/official-source validation.")
    if quality["score_out_of_10"] < 8.5:
        production_reasons.append("Confidence score is below the production threshold of 8.5/10.")

    return {
        "missing_required_fields": missing_required,
        "needs_validation": needs_validation,
        "can_create_symbols": not any(item["tool"] == "create_symbol" for item in missing_required),
        "can_create_footprint_draft": not any(
            item["tool"] == "create_footprint" for item in missing_required
        ),
        "can_create_footprint_without_review": not footprint["validation_required"],
        "quality": quality,
        "production_gate": {
            "pass": not production_reasons,
            "threshold_score_out_of_10": 8.5,
            "reasons": production_reasons,
        },
    }


def package_rule_matches(extracted: dict[str, Any], rule: dict[str, Any]) -> bool:
    match = rule.get("package_match", {})
    if not match:
        return False
    document = extracted.get("document", {})
    symbol = extracted.get("symbol_creation", {})
    package = extracted.get("footprint_creation", {}).get("package", {})

    manufacturer = match.get("manufacturer")
    if manufacturer and manufacturer.lower() != str(document.get("manufacturer", "")).lower():
        return False

    package_suffix = match.get("package_suffix")
    if package_suffix and package_suffix != str(symbol.get("package_suffix") or package.get("suffix", "")):
        return False

    package_name_contains = match.get("package_name_contains")
    if package_name_contains and package_name_contains.lower() not in str(package.get("name", "")).lower():
        return False

    pin_count = match.get("pin_count")
    if pin_count and int(pin_count) != int(symbol.get("pin_count", 0)):
        return False

    package_families = match.get("package_name_any_contains", [])
    if package_families:
        name = str(package.get("name") or symbol.get("package_name", "")).lower()
        if not any(str(item).lower() in name for item in package_families):
            return False

    return True


def select_package_rule(
    extracted: dict[str, Any],
    package_rules: dict[str, Any],
    requested: str,
) -> tuple[str, dict[str, Any]]:
    if requested and requested != "auto":
        if requested not in package_rules:
            raise ValueError(f"Unknown package rule id: {requested}")
        return requested, package_rules[requested]

    fp = extracted.get("footprint_creation", {})
    if explicit_pads_from_schema(fp) and "GENERIC_EXPLICIT_PADS" in package_rules:
        return "GENERIC_EXPLICIT_PADS", package_rules["GENERIC_EXPLICIT_PADS"]

    for rule_id, rule in package_rules.items():
        if rule_id == "GENERIC_EXPLICIT_PADS":
            continue
        if package_rule_matches(extracted, rule):
            return rule_id, rule

    if "GENERIC_DUAL_ROW_SMD" in package_rules:
        return "GENERIC_DUAL_ROW_SMD", package_rules["GENERIC_DUAL_ROW_SMD"]
    raise ValueError("No package rule matched and GENERIC_DUAL_ROW_SMD is unavailable.")


def footprint_name_for(extracted: dict[str, Any], override: str = "") -> str:
    if override:
        return override
    fp = extracted.get("footprint_creation", {})
    package = fp.get("package", {})
    symbol = extracted.get("symbol_creation", {})
    package_name = package.get("name") or symbol.get("package_name") or "Package"
    family = symbol.get("base_part_family") or "Component"
    return slugify(f"{package_name}_{family}", "Generated_Footprint")


def resolve(args: argparse.Namespace) -> dict[str, Any]:
    extracted = load_json(Path(args.input))
    review_path = Path(args.review_overrides).resolve() if args.review_overrides else None
    review = load_optional_json(review_path)
    package_rules = load_package_rules(Path(args.package_rules))
    package_rule_id, package_rule = select_package_rule(
        extracted, package_rules, args.package_rule_id
    )

    library_root = Path(args.library_root).resolve()
    component_slug = slugify(component_label(extracted, args.component_id))
    symbol_library_name = args.symbol_library_name or f"{component_slug}_Custom"
    footprint_library_name = args.footprint_library_name or f"{component_slug}_Custom"
    symbol_library = library_root / f"{symbol_library_name}.kicad_sym"
    footprint_library = library_root / f"{footprint_library_name}.pretty"
    footprint_name = footprint_name_for(extracted, args.footprint_name)
    footprint_ref = f"{footprint_library.stem}:{footprint_name}"

    variants = get_symbol_variants(extracted["symbol_creation"])
    symbols = {
        variant["id"]: build_symbol_payload_generic(
            extracted,
            variant,
            symbol_library,
            footprint_ref,
            args.overwrite,
        )
        for variant in variants
    }

    selected_rule = {"id": package_rule_id, **package_rule}
    generator = package_rule.get("footprint_rule", {}).get("generator")
    if generator == "acs37010_lz_soic6":
        footprint = build_footprint_payload(
            extracted,
            footprint_library,
            footprint_name,
            args.orientation,
            args.overwrite,
            review,
            selected_rule,
        )
    else:
        footprint = build_footprint_payload_generic(
            extracted,
            footprint_library,
            footprint_name,
            args.overwrite,
            review,
            selected_rule,
            args.orientation,
        )

    result = {
        "source": {
            "extracted_data": str(Path(args.input).resolve()),
            "guide": str(Path(args.guide).resolve()),
            "review_overrides": str(review_path) if review_path else None,
            "package_rules": str(Path(args.package_rules).resolve()),
        },
        "rule_policy": {
            "datasheet_exact": "Copied from extracted datasheet facts.",
            "derived_by_rule": "Generated deterministically from package/symbol rules.",
            "needs_validation": "Ambiguous in source datasheet; review before production use.",
            "user_config": "Project or library setting, not a datasheet field.",
            "review_confirmed": "Cleared by an explicit review override backed by evidence.",
        },
        "selected_package_rule": selected_rule,
        "mcp_payloads": {
            "symbols": symbols,
            "footprint": footprint,
            "registration": {
                "symbol_library": {
                    "libraryPath": str(symbol_library),
                    "libraryName": symbol_library_name,
                    "description": f"Custom {component_slug} symbols",
                    "scope": "global",
                },
                "footprint_library": {
                    "libraryPath": str(footprint_library),
                    "libraryName": footprint_library_name,
                    "description": f"Custom {component_slug} footprints",
                    "scope": "global",
                },
            },
        },
    }
    result["validation_summary"] = build_validation_summary(result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve MinerU datasheet extraction into KiCad MCP creator payloads."
    )
    parser.add_argument(
        "--input",
        default="extracted/ACS37010_symbol_footprint_data.json",
        help="Path to extracted datasheet JSON.",
    )
    parser.add_argument(
        "--output",
        default="extracted/ACS37010_kicad_mcp_payloads.json",
        help="Path to write MCP payload JSON.",
    )
    parser.add_argument(
        "--library-root",
        default="extracted/kicad_libraries",
        help="Directory where generated KiCad libraries should live.",
    )
    parser.add_argument(
        "--guide",
        default="kicad_mcp/docs/FOOTPRINT_SYMBOL_CREATOR_GUIDE.md",
        help="Guide used for schema compatibility reference.",
    )
    parser.add_argument(
        "--orientation",
        default="branding_reference_as_drawn",
        help="Candidate package orientation rule to apply.",
    )
    parser.add_argument(
        "--package-rules",
        default=str(DEFAULT_PACKAGE_RULES),
        help="Package rule registry JSON.",
    )
    parser.add_argument(
        "--package-rule-id",
        default="auto",
        help="Package rule id to apply from the registry, or auto.",
    )
    parser.add_argument(
        "--component-id",
        default="",
        help="Optional stable component/library id. Defaults to normalized schema metadata.",
    )
    parser.add_argument(
        "--symbol-library-name",
        default="",
        help="Optional KiCad symbol library nickname/file stem.",
    )
    parser.add_argument(
        "--footprint-library-name",
        default="",
        help="Optional KiCad footprint library nickname/directory stem.",
    )
    parser.add_argument(
        "--footprint-name",
        default="",
        help="Optional generated footprint name.",
    )
    parser.add_argument(
        "--review-overrides",
        default="",
        help="Optional JSON file containing human/official-source confirmations.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Set overwrite=true in MCP create payloads.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = resolve(args)
    write_json(Path(args.output), result)
    summary = result["validation_summary"]
    print(f"Wrote {args.output}")
    print(f"can_create_symbols={summary['can_create_symbols']}")
    print(f"can_create_footprint_draft={summary['can_create_footprint_draft']}")
    print(f"can_create_footprint_without_review={summary['can_create_footprint_without_review']}")
    print(f"confidence_score_out_of_10={summary['quality']['score_out_of_10']}")
    print(f"production_gate_pass={summary['production_gate']['pass']}")
    if summary["needs_validation"]:
        print("needs_validation:")
        for item in summary["needs_validation"]:
            print(f"  - {item['tool']}: {item['field']}")


if __name__ == "__main__":
    main()
