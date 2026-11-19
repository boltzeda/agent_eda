"""Extract component and connectivity records from a KiCad .net file."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .sexpr import child, children, parse, value


def _property_value(component: list, name: str) -> str:
    wanted = name.strip().lower()
    for prop in children(component, "property"):
        key = value(prop, "name").strip().lower()
        if key == wanted:
            return value(prop, "value")
    fields = child(component, "fields")
    if fields:
        for field in children(fields, "field"):
            key = value(field, "name").strip().lower()
            if key != wanted:
                continue
            nested = child(field, "value")
            if nested and len(nested) > 1:
                return str(nested[1])
            if len(field) > 2 and not isinstance(field[2], list):
                return str(field[2])
    return ""


def _pin_records(root: list) -> dict[tuple[str, str], list[dict[str, str]]]:
    records: dict[tuple[str, str], list[dict[str, str]]] = {}
    libparts = child(root, "libparts")
    if libparts is None:
        return records
    for libpart in children(libparts, "libpart"):
        lib = value(libpart, "lib")
        part = value(libpart, "part")
        pins = child(libpart, "pins")
        values = []
        if pins:
            for pin in children(pins, "pin"):
                values.append({
                    "pin": value(pin, "num"),
                    "name": value(pin, "name"),
                    "type": value(pin, "type"),
                })
        records[(lib, part)] = values
    return records


def _libpart_metadata(root: list) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    libparts = child(root, "libparts")
    if libparts is None:
        return records
    for libpart in children(libparts, "libpart"):
        key = (value(libpart, "lib"), value(libpart, "part"))
        filters = []
        footprints = child(libpart, "footprints")
        if footprints:
            filters = [str(fp[1]) for fp in children(footprints, "fp") if len(fp) > 1]
        fields: dict[str, str] = {}
        field_group = child(libpart, "fields")
        if field_group:
            for field in children(field_group, "field"):
                field_name = value(field, "name")
                if field_name:
                    fields[field_name] = str(field[2]) if len(field) > 2 and not isinstance(field[2], list) else ""
        records[key] = {
            "description": value(libpart, "description"),
            "footprint_filters": filters,
            "fields": fields,
        }
    return records


def extract_netlist(netlist_path: Path) -> dict[str, Any]:
    netlist_path = netlist_path.expanduser().resolve()
    try:
        root = parse(netlist_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"cannot parse KiCad netlist {netlist_path}: {exc}") from exc
    if not root or root[0] != "export":
        raise ValueError(f"not a KiCad exported netlist: {netlist_path}")

    pin_map = _pin_records(root)
    libpart_map = _libpart_metadata(root)
    component_group = child(root, "components")
    components: list[dict[str, str]] = []
    footprints: dict[str, dict[str, Any]] = {}
    footprint_rows: list[dict[str, Any]] = []
    if component_group:
        for comp in children(component_group, "comp"):
            ref = value(comp, "ref").strip()
            if not ref:
                continue
            source = child(comp, "libsource")
            lib = value(source, "lib") if source else ""
            part = value(source, "part") if source else ""
            libpart = libpart_map.get((lib, part), {})
            footprint = value(comp, "footprint") or _property_value(comp, "Footprint")
            description = (
                _property_value(comp, "Description")
                or (value(comp, "description") if value(comp, "description") else "")
                or str(libpart.get("description") or "")
            )
            datasheet = _property_value(comp, "Datasheet") or value(comp, "datasheet")
            sheetpath_node = child(comp, "sheetpath")
            sheetpath = value(sheetpath_node, "names") if sheetpath_node else ""
            value_text = value(comp, "value")
            components.append({
                "ref": ref,
                "value": value_text,
                "footprint": footprint,
                "datasheet": datasheet,
                "description": description,
                "symbol_library": lib,
                "symbol_part": part,
                "sheetpath": sheetpath,
            })
            pin_count = len(pin_map.get((lib, part), []))
            footprint_name = footprint.split(":", 1)[-1] if footprint else ""
            footprint_library = footprint.split(":", 1)[0] if ":" in footprint else ""
            if footprint:
                footprint_rows.append({
                    "ref": ref,
                    "value": value_text,
                    "footprint": footprint,
                    "footprint_library": footprint_library,
                    "footprint_name": footprint_name,
                    "description": description,
                    "footprint_filters": ";".join(libpart.get("footprint_filters", [])),
                    "pin_count": pin_count,
                })
            footprints[ref] = {
                "class": footprint or "NoFootprint",
                "fallback_class": footprint_name or "Unknown",
                "kicad_footprint": footprint,
                "dimension_source": "footprint_id_only",
                "geometry_source": "not_in_netlist",
                "pin_count": pin_count,
            }

    nets: list[dict[str, str | int]] = []
    net_nodes: list[dict[str, str]] = []
    net_group = child(root, "nets")
    if net_group:
        for net in children(net_group, "net"):
            code = value(net, "code")
            name = value(net, "name")
            net_class = value(net, "class")
            nodes = children(net, "node")
            nets.append({"code": code, "name": name, "fanout": len(nodes), "class": net_class})
            for node in nodes:
                net_nodes.append({
                    "net": name,
                    "code": code,
                    "ref": value(node, "ref"),
                    "pin": value(node, "pin"),
                    "pinfunction": value(node, "pinfunction"),
                    "pintype": value(node, "pintype"),
                })

    design = child(root, "design")
    return {
        "source": str(netlist_path),
        "components": sorted(components, key=lambda row: row["ref"]),
        "nets": sorted(nets, key=lambda row: (str(row["code"]), str(row["name"]))),
        "net_nodes": sorted(net_nodes, key=lambda row: (row["net"], row["ref"], row["pin"])),
        "footprint_rows": sorted(footprint_rows, key=lambda row: row["ref"]),
        "footprints": footprints,
        "netlist_metadata": {
            "version": value(root, "version"),
            "date": value(design, "date") if design else "",
            "tool": value(design, "tool") if design else "",
            "component_prefix_counts": dict(Counter(
                "".join(char for char in row["ref"] if char.isalpha())
                for row in components
            )),
            "footprint_count": sum(bool(row["footprint"]) for row in components),
        },
    }
