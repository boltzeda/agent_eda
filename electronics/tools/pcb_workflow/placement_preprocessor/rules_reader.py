"""Optional extraction of supported KiCad project and custom DRC rules."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from .sexpr import children, parse_many, value


PROJECT_MINIMUM_RULES = (
    "min_clearance", "min_track_width", "min_via_diameter",
    "min_via_annular_width", "min_hole_clearance",
    "min_copper_edge_clearance", "min_through_hole_diameter",
    "min_microvia_diameter", "min_microvia_drill", "min_hole_to_hole",
)
CLASS_TO_RULE = {
    "clearance": "clearance",
    "track_width": "trace_width",
    "via_diameter": "via_track_width",
    "via_drill": "via_drill",
}

def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_project_rules(
    project_path: Path,
    netlist_nets: list[dict[str, Any]],
    board_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_path = project_path.expanduser().resolve()
    if project_path.suffix.lower() != ".kicad_pro":
        raise ValueError("project settings input must have a .kicad_pro extension")
    try:
        project = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot parse KiCad project settings {project_path}: {exc}") from exc
    if not isinstance(project, dict):
        raise ValueError("KiCad project settings must contain a JSON object")

    board_setup = ((project.get("board") or {}).get("design_settings") or {})
    minimums = ((board_setup.get("rules") or {}))
    minimum_rules = {
        source: numeric
        for source in PROJECT_MINIMUM_RULES
        if (numeric := _number(minimums.get(source))) is not None
    }

    net_settings = project.get("net_settings") or {}
    class_rows = net_settings.get("classes") or []
    classes = {str(row.get("name") or ""): row for row in class_rows if isinstance(row, dict)}
    default_class = classes.get("Default") or {}

    net_to_class: dict[str, str] = {}
    for net_name, class_name in (net_settings.get("netclass_assignments") or {}).items():
        net_to_class[str(net_name)] = str(class_name)
    patterns = net_settings.get("netclass_patterns") or []
    for assignment in patterns:
        if not isinstance(assignment, dict):
            continue
        pattern = str(assignment.get("pattern") or "")
        class_name = str(assignment.get("netclass") or "")
        if not pattern or not class_name:
            continue
        for net in netlist_nets:
            name = str(net.get("name") or "")
            if fnmatch.fnmatchcase(name, pattern):
                net_to_class[name] = class_name

    for net in netlist_nets:
        name = str(net.get("name") or "")
        schematic_class = str(net.get("class") or "")
        if schematic_class in classes and schematic_class != "Default":
            net_to_class.setdefault(name, schematic_class)

    embedded = (board_rules or {}).get("setup_values") or {}
    drc: dict[str, Any] = {
        "sources": {"project": str(project_path)},
        "minimum_rules": minimum_rules,
        "rule_severities": board_setup.get("rule_severities") or {},
    }
    if board_rules:
        drc["board_embedded_rules"] = board_rules

    def assign_if_missing(key: str, *candidates: Any) -> None:
        if drc.get(key) is not None:
            return
        for candidate in candidates:
            number = _number(candidate)
            if number is not None:
                drc[key] = number
                return

    assign_if_missing("clearance", default_class.get("clearance"), minimums.get("min_clearance"), embedded.get("trace_clearance"))
    assign_if_missing("trace_width", default_class.get("track_width"), minimums.get("min_track_width"), embedded.get("trace_min"), embedded.get("last_trace_width"))
    assign_if_missing("via_track_width", default_class.get("via_diameter"), minimums.get("min_via_diameter"), embedded.get("via_min_size"), embedded.get("via_size"))
    assign_if_missing("via_drill", default_class.get("via_drill"), embedded.get("via_min_drill"), embedded.get("via_drill"))
    assign_if_missing("via_annular_ring", minimums.get("min_via_annular_width"))
    assign_if_missing("hole_clearance", minimums.get("min_hole_clearance"), embedded.get("hole_clearance"))
    assign_if_missing("outline_clearance", minimums.get("min_copper_edge_clearance"), embedded.get("edge_clearance"))

    default_via_diameter = _number(default_class.get("via_diameter"))
    default_via_drill = _number(default_class.get("via_drill"))
    if drc.get("via_annular_ring") is None and default_via_diameter is not None and default_via_drill is not None:
        drc["via_annular_ring"] = max((default_via_diameter - default_via_drill) / 2.0, 0.0)

    net_classes = []
    for class_name, class_row in classes.items():
        if not class_name or class_name == "Default":
            continue
        assigned_nets = sorted(net for net, selected in net_to_class.items() if selected == class_name)
        if not assigned_nets:
            continue
        item: dict[str, Any] = {"name": class_name, "nets": assigned_nets}
        for source, target in CLASS_TO_RULE.items():
            numeric = _number(class_row.get(source))
            if numeric is not None:
                item[target] = numeric
        class_drill = _number(class_row.get("via_drill"))
        class_diameter = _number(class_row.get("via_diameter"))
        if class_drill is not None and class_diameter is not None:
            item["via_annular_ring"] = max((class_diameter - class_drill) / 2.0, 0.0)
        net_classes.append(item)
    if net_classes:
        drc["net_classes"] = net_classes
    return drc


def extract_board_rules(board_rules: dict[str, Any], board_path: Path) -> dict[str, Any] | None:
    """Map board-file setup values without treating local zone values as defaults."""
    setup = (board_rules or {}).get("setup_values") or {}
    if not setup and not (board_rules or {}).get("zone_clearances"):
        return None
    drc: dict[str, Any] = {
        "sources": {"board": str(board_path.expanduser().resolve())},
        "board_embedded_rules": board_rules,
        "minimum_rules": {
            name: setup[name]
            for name in ("trace_min", "via_min_size", "via_min_drill", "hole_clearance", "edge_clearance")
            if name in setup
        },
    }
    for source, target in (
        ("trace_clearance", "clearance"),
        ("last_trace_width", "trace_width"),
        ("via_size", "via_track_width"),
        ("via_drill", "via_drill"),
        ("hole_clearance", "hole_clearance"),
        ("edge_clearance", "outline_clearance"),
    ):
        numeric = _number(setup.get(source))
        if numeric is not None:
            drc[target] = numeric
    return drc


def extract_custom_rules(rules_path: Path) -> dict[str, Any]:
    rules_path = rules_path.expanduser().resolve()
    if rules_path.suffix.lower() != ".kicad_dru":
        raise ValueError("custom rules input must have a .kicad_dru extension")
    try:
        expressions = parse_many(rules_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"cannot parse KiCad custom rules {rules_path}: {exc}") from exc
    if len(expressions) == 1 and expressions[0] and expressions[0][0] == "kicad_dru":
        root = expressions[0]
        rule_nodes = children(root, "rule")
        version = value(root, "version")
    else:
        version_nodes = [item for item in expressions if item and item[0] == "version"]
        rule_nodes = [item for item in expressions if item and item[0] == "rule"]
        if not version_nodes and not rule_nodes:
            raise ValueError(f"not a KiCad custom design-rules file: {rules_path}")
        version = str(version_nodes[0][1]) if version_nodes and len(version_nodes[0]) > 1 else ""
    custom_rules = []
    for rule in rule_nodes:
        constraints = []
        for constraint in children(rule, "constraint"):
            constraints.append({
                "type": str(constraint[1]) if len(constraint) > 1 else "",
                "arguments": constraint[2:],
            })
        custom_rules.append({
            "name": str(rule[1]) if len(rule) > 1 else "",
            "layer": value(rule, "layer"),
            "condition": value(rule, "condition"),
            "constraints": constraints,
            "source_expression": rule,
        })
    return {
        "source": str(rules_path),
        "version": version,
        "rule_count": len(custom_rules),
        "rules": custom_rules,
        "enforced_by_placement_or_router": False,
    }
