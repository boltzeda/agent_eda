"""Extract placement inputs from a KiCad PCB file without editing it."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .sexpr import child, children, number, parse, value


GEOMETRY_TAGS = {"fp_line", "fp_rect", "fp_arc", "fp_circle", "fp_poly", "fp_curve"}
BOARD_EDGE_TAGS = {"gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly", "gr_curve"}


def _xy(item: list | None) -> tuple[float, float] | None:
    if item is None or len(item) < 3:
        return None
    try:
        return number(item[1], "x"), number(item[2], "y")
    except ValueError:
        return None


def _points(node: list) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for tag in ("start", "mid", "end", "center"):
        point = _xy(child(node, tag))
        if point is not None:
            result.append(point)
    pts = child(node, "pts")
    if pts is not None:
        for xy in children(pts, "xy"):
            point = _xy(xy)
            if point is not None:
                result.append(point)
    return result


def _layer(node: list) -> str:
    return value(node, "layer")


def _primitive_points(node: list) -> list[tuple[float, float]]:
    tag = str(node[0])
    points = _points(node)
    if tag == "fp_circle" or tag == "gr_circle":
        center = _xy(child(node, "center"))
        end = _xy(child(node, "end"))
        if center and end:
            radius = math.hypot(end[0] - center[0], end[1] - center[1])
            points.extend(((center[0] - radius, center[1] - radius),
                           (center[0] + radius, center[1] + radius)))
    return points


def _rotate(point: tuple[float, float], degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    x, y = point
    return x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)


def _pad_polygon(shape: str, x: float, y: float, width: float, height: float,
                 rotation: float) -> list[list[float]]:
    if width <= 0 or height <= 0:
        return []
    if shape == "circle":
        points = [(width / 2 * math.cos(i * 2 * math.pi / 32),
                   height / 2 * math.sin(i * 2 * math.pi / 32)) for i in range(32)]
    elif shape == "oval":
        # A sampled oval gives the same bounding dimensions and a safe local
        # outline for the placement constraint geometry.
        points = [(width / 2 * math.cos(i * 2 * math.pi / 32),
                   height / 2 * math.sin(i * 2 * math.pi / 32)) for i in range(32)]
    else:
        points = [(-width / 2, -height / 2), (width / 2, -height / 2),
                  (width / 2, height / 2), (-width / 2, height / 2)]
    transformed = []
    for px, py in points:
        rx, ry = _rotate((px, py), rotation)
        transformed.append([x + rx, y + ry])
    return transformed


def _pad_info(pad: list) -> dict[str, Any] | None:
    if len(pad) < 4:
        return None
    number_text = str(pad[1])
    pad_type = str(pad[2])
    shape = str(pad[3])
    at = child(pad, "at")
    size = child(pad, "size")
    if at is None or size is None or len(at) < 3 or len(size) < 3:
        return None
    x, y = number(at[1], "pad x"), number(at[2], "pad y")
    rotation = number(at[3], "pad rotation") if len(at) > 3 else 0.0
    width, height = abs(number(size[1], "pad width")), abs(number(size[2], "pad height"))
    layers_node = child(pad, "layers")
    layers = [str(item) for item in layers_node[1:]] if layers_node else []
    net_node = child(pad, "net")
    net_name = ""
    if net_node:
        strings = [str(item) for item in net_node[1:] if not isinstance(item, list)]
        if strings:
            net_name = strings[-1]
    drill_node = child(pad, "drill")
    drill: list[float] = []
    if drill_node:
        for item in drill_node[1:]:
            if isinstance(item, str):
                try:
                    drill.append(abs(float(item)))
                except ValueError:
                    continue
    return {
        "number": number_text,
        "type": pad_type,
        "shape": shape,
        "x": x,
        "y": y,
        "rotation": rotation,
        "width": width,
        "height": height,
        "layers": layers,
        "net": net_name,
        "drill": drill,
    }


def _property(fp: list, key: str, legacy: str) -> str:
    for prop in children(fp, "property"):
        if len(prop) > 2 and str(prop[1]) == key:
            return str(prop[2])
    for text in children(fp, "fp_text"):
        if len(text) > 2 and str(text[1]).lower() == legacy:
            return str(text[2])
    return ""


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _rect_polygon(bounds: tuple[float, float, float, float], cx: float, cy: float) -> list[list[float]]:
    x1, y1, x2, y2 = bounds
    return [[round(x1 - cx, 6), round(-(y1 - cy), 6)],
            [round(x2 - cx, 6), round(-(y1 - cy), 6)],
            [round(x2 - cx, 6), round(-(y2 - cy), 6)],
            [round(x1 - cx, 6), round(-(y2 - cy), 6)]]


def _footprint_metadata(fp: list) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ref = _property(fp, "Reference", "reference")
    fp_name = str(fp[1]) if len(fp) > 1 else ""
    pads = [_pad_info(pad) for pad in children(fp, "pad")]
    pads = [pad for pad in pads if pad is not None]

    all_points: list[tuple[float, float]] = []
    courtyard_points: list[tuple[float, float]] = []
    for item in fp:
        if not isinstance(item, list) or not item or item[0] not in GEOMETRY_TAGS:
            continue
        item_points = _primitive_points(item)
        all_points.extend(item_points)
        layer = _layer(item)
        if layer.endswith(".CrtYd"):
            courtyard_points.extend(item_points)

    for pad in pads:
        polygon = _pad_polygon(pad["shape"], pad["x"], pad["y"],
                               pad["width"], pad["height"], pad["rotation"])
        all_points.extend((point[0], point[1]) for point in polygon)

    bounds = _bbox(all_points)
    if bounds is None:
        width = height = 3.0
        bounds = (-width / 2, -height / 2, width / 2, height / 2)
        dimension_source = "fallback_3mm"
    else:
        width = max(bounds[2] - bounds[0], 0.1)
        height = max(bounds[3] - bounds[1], 0.1)
        dimension_source = "board_embedded_geometry"
    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2

    courtyard_bounds = _bbox(courtyard_points)
    courtyard = [_rect_polygon(courtyard_bounds, cx, cy)] if courtyard_bounds else []
    body = [_rect_polygon(bounds, cx, cy)]
    pad_geometry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pad in pads:
        poly = _pad_polygon(pad["shape"], pad["x"], pad["y"],
                             pad["width"], pad["height"], pad["rotation"])
        shifted = [[round(point[0] - cx, 6), round(-(point[1] - cy), 6)] for point in poly]
        pad_geometry[pad["number"]].append({
            "center": [round(pad["x"] - cx, 6), round(-(pad["y"] - cy), 6)],
            "size": [round(pad["width"], 6), round(pad["height"], 6)],
            "rotation": round(-pad["rotation"], 6),
            "shape": pad["shape"],
            "layers": pad["layers"],
            "polygons": [shifted] if shifted else [],
        })

    geometry = {
        "coordinate_system": "visual_center_y_up",
        "origin_to_center": {"x": round(cx, 6), "y": round(-cy, 6)},
        "courtyard": courtyard,
        "body": body,
        "pads": dict(pad_geometry),
        "geometry_source": "board_embedded_geometry",
    }
    metadata = {
        "class": fp_name,
        "width": round(width, 6),
        "height": round(height, 6),
        "pin_count": len(pads),
        "fallback_class": fp_name,
        "kicad_footprint": fp_name,
        "dimension_source": dimension_source,
        "geometry": geometry,
    }
    return {"ref": ref, "value": _property(fp, "Value", "value"),
            "footprint": fp_name, "datasheet": _property(fp, "Datasheet", "datasheet"),
            "metadata": metadata, "pads": pads}


def _edge_coordinates(root: list) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for node in root:
        if not isinstance(node, list) or not node or node[0] not in BOARD_EDGE_TAGS:
            continue
        if _layer(node) == "Edge.Cuts":
            points.extend(_primitive_points(node))
    return points


def _layer_config(root: list) -> dict[str, Any]:
    layers_node = child(root, "layers")
    copper = []
    if layers_node:
        for item in layers_node[1:]:
            if isinstance(item, list) and len(item) >= 3:
                name = str(item[1])
                kind = str(item[2])
                if name.endswith(".Cu") and kind == "signal":
                    copper.append(name)
    fills: dict[str, str] = {}
    for zone in children(root, "zone"):
        net = value(zone, "net")
        layer = value(zone, "layer")
        if net and layer:
            fills[layer] = net
    return {"total_layers": len(copper), "copper_layers": copper, "fills": fills}


def _embedded_rules(root: list) -> dict[str, Any]:
    """Collect rule-like values actually serialized in the board file."""
    setup = child(root, "setup")
    names = {
        "trace_clearance", "trace_min", "last_trace_width", "via_size",
        "via_drill", "via_min_size", "via_min_drill", "edge_clearance",
        "hole_clearance", "zone_clearance", "pad_to_mask_clearance",
        "pad_to_paste_clearance", "solder_mask_min_width",
    }
    setup_values: dict[str, float] = {}
    if setup:
        for item in setup:
            if isinstance(item, list) and len(item) > 1 and item[0] in names:
                try:
                    setup_values[str(item[0])] = number(item[1], str(item[0]))
                except ValueError:
                    continue
    zone_values = []
    for zone in children(root, "zone"):
        clearance_node = child(child(zone, "connect_pads") or [], "clearance")
        if clearance_node and len(clearance_node) > 1:
            try:
                clearance = number(clearance_node[1], "zone clearance")
            except ValueError:
                continue
            zone_values.append({
                "net": value(zone, "net"),
                "layer": value(zone, "layer"),
                "clearance": clearance,
            })
    return {"setup_values": setup_values, "zone_clearances": zone_values}


def _keepouts(root: list, origin_x: float, max_y: float) -> list[dict[str, Any]]:
    result = []
    for zone in children(root, "zone"):
        if child(zone, "keepout") is None:
            continue
        polygon = child(zone, "polygon")
        pts = child(polygon, "pts") if polygon else None
        coords = [_xy(item) for item in children(pts, "xy")] if pts else []
        coords = [point for point in coords if point is not None]
        bounds = _bbox(coords)
        if bounds:
            result.append({
                "x1": round(bounds[0] - origin_x, 6),
                # Placement geometry uses a y-up local board coordinate frame;
                # KiCad's serialized board coordinates use y-down.
                "y1": round(max_y - bounds[3], 6),
                "x2": round(bounds[2] - origin_x, 6),
                "y2": round(max_y - bounds[1], 6),
                "source": "board_keepout_zone",
            })
    return result


def extract_board(board_path: Path) -> dict[str, Any]:
    try:
        root = parse(board_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"cannot parse KiCad board {board_path}: {exc}") from exc
    if not root or root[0] != "kicad_pcb":
        raise ValueError(f"not a KiCad PCB S-expression: {board_path}")

    outline = _bbox(_edge_coordinates(root))
    if outline is None or outline[2] <= outline[0] or outline[3] <= outline[1]:
        raise ValueError("board has no usable Edge.Cuts outline; cannot infer its dimensions")
    origin_x, origin_y, max_x, max_y = outline
    board_width, board_height = max_x - origin_x, max_y - origin_y

    components = []
    footprints = {}
    net_nodes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    holes = []
    for fp in children(root, "footprint") + children(root, "module"):
        record = _footprint_metadata(fp)
        ref = record["ref"].strip()
        if not ref:
            continue
        components.append({key: record[key] for key in ("ref", "value", "footprint", "datasheet")})
        footprints[ref] = record["metadata"]
        fp_at = child(fp, "at")
        fp_x = number(fp_at[1], f"{ref} x") if fp_at and len(fp_at) > 2 else 0.0
        fp_y = number(fp_at[2], f"{ref} y") if fp_at and len(fp_at) > 2 else 0.0
        fp_rotation = number(fp_at[3], f"{ref} rotation") if fp_at and len(fp_at) > 3 else 0.0
        for pad in record["pads"]:
            if pad["net"]:
                net_nodes[pad["net"]].append((ref, pad["number"]))
            if pad["type"] == "np_thru_hole" and pad["drill"]:
                pad_x, pad_y = _rotate((pad["x"], pad["y"]), fp_rotation)
                diameter = max(pad["drill"])
                holes.append({
                    "x": round(fp_x + pad_x - origin_x, 6),
                    "y": round(board_height - (fp_y + pad_y - origin_y), 6),
                    "diameter": round(diameter, 6),
                    "radius": round(diameter / 2.0, 6),
                    "ref": ref,
                    "pad": pad["number"],
                })

    net_rows = [{"code": "", "name": name, "fanout": len(nodes)}
                for name, nodes in sorted(net_nodes.items())]
    node_rows = [{"net": net, "ref": ref, "pin": pin}
                 for net in sorted(net_nodes)
                 for ref, pin in sorted(net_nodes[net])]

    return {
        "components": sorted(components, key=lambda row: row["ref"]),
        "nets": net_rows,
        "net_nodes": node_rows,
        "footprints": footprints,
        "board": {
            "width": round(board_width, 6),
            "height": round(board_height, 6),
            "size_source": "Edge.Cuts bounding box",
            "source_origin": {"x": round(origin_x, 6), "y": round(max_y, 6)},
            "source_bounds": {
                "min_x": round(origin_x, 6), "min_y": round(origin_y, 6),
                "max_x": round(max_x, 6), "max_y": round(max_y, 6),
            },
            "placement_coordinate_frame": "board-local origin at Edge.Cuts lower-left",
        },
        "layers": _layer_config(root),
        "embedded_rules": _embedded_rules(root),
        "mechanical": {
            "holes": holes,
            "keepouts": _keepouts(root, origin_x, max_y),
            "mechanical_keepouts": _keepouts(root, origin_x, max_y),
            "connector_keepouts": [],
            "connector_edges": {},
        },
    }
