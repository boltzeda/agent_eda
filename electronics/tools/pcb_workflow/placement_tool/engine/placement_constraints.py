#!/usr/bin/env python3
"""Board-independent, geometry-aware placement constraints.

The existing placer intentionally represents a component by its visual-centre
coordinate.  This module does not change that contract.  It supplies an
opt-in layer that transforms footprint-local courtyards and pads around that
same centre, measures user constraints, seeds useful candidate positions, and
produces machine-readable validation reports.
"""

from __future__ import annotations

import math
from copy import deepcopy

from pcb_workflow.kicad_environment import import_pcbnew
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from geometry_utils import coerce_size, extract_pose, extract_pose_with_side


PAIR_MIN_TYPES = {"minimum_clearance", "keep_apart"}
PAIR_MAX_TYPES = {"maximum_distance", "pad_proximity", "keep_together"}
PAIR_TYPES = PAIR_MIN_TYPES | PAIR_MAX_TYPES | {"align", "relative_position"}
SINGLE_TYPES = {
    "required_side", "preferred_side", "orientation", "board_edge_distance",
    "inside_region", "outside_region", "fixed_position",
}
SUPPORTED_TYPES = PAIR_TYPES | SINGLE_TYPES


def _as_ref(endpoint):
    if isinstance(endpoint, str):
        return endpoint
    if isinstance(endpoint, dict):
        return str(endpoint.get("ref") or "")
    return ""


def _endpoint(value, default_geometry="component"):
    if isinstance(value, str):
        return {"ref": value, "geometry": default_geometry}
    result = dict(value or {})
    result["ref"] = str(result.get("ref") or "")
    result.setdefault("geometry", "pad" if result.get("pad") is not None else default_geometry)
    if result.get("pad") is not None:
        result["pad"] = str(result["pad"])
    return result


def _number(value, name, *, minimum=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric, got {value!r}") from None
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name} must be finite and >= {minimum}, got {value!r}")
    return result


def normalize_constraints(raw_constraints, known_refs, footprint_metadata,
                          *, allow_bottom=False):
    """Validate and normalize the generic placement constraint list.

    Bad references, missing pads, and unavailable exact geometry are errors.
    Silent dropping would make a placement report look successful when the
    requested physical relationship was never evaluated.
    """
    if not raw_constraints:
        return []
    if not isinstance(raw_constraints, list):
        raise ValueError("placement.relative_constraints must be a JSON list")

    known_refs = {str(ref) for ref in known_refs}
    normalized = []
    for index, original in enumerate(raw_constraints):
        if not isinstance(original, dict):
            raise ValueError(f"placement constraint #{index + 1} must be an object")
        item = deepcopy(original)
        kind = str(item.get("type") or "").strip().lower()
        aliases = {
            "min_clearance": "minimum_clearance",
            "max_distance": "maximum_distance",
            "required_orientation": "orientation",
            "relative": "relative_position",
            "relative_offset": "relative_position",
        }
        kind = aliases.get(kind, kind)
        if kind not in SUPPORTED_TYPES:
            raise ValueError(
                f"placement constraint #{index + 1} has unsupported type {kind!r}; "
                f"supported: {sorted(SUPPORTED_TYPES)}"
            )
        item["type"] = kind
        item["id"] = str(item.get("id") or f"constraint_{index + 1}")
        item["hard"] = bool(item.get("hard", kind != "preferred_side"))
        item["weight"] = _number(item.get("weight", 1.0), f"{item['id']}.weight", minimum=0.0)

        if kind in PAIR_TYPES:
            # Accept the common serialized spellings used by clients and
            # older prompts, but normalize them to the one internal schema.
            # This is deliberately limited to pair constraints so that
            # ``target`` keeps its established meaning for single-target
            # constraints.
            if item.get("a") is None:
                item["a"] = item.get("from", item.get("source", item.get("ref1")))
            if item.get("b") is None:
                item["b"] = item.get(
                    "to", item.get("target", item.get("target_ref", item.get("ref2")))
                )
            if item.get("a") is None or item.get("b") is None:
                raise ValueError(
                    f"{item['id']} requires pair endpoints 'a' and 'b' "
                    "(aliases: from/to, source/target_ref, or ref1/ref2)"
                )
            item["a"] = _endpoint(item["a"])
            item["b"] = _endpoint(item["b"])

            # Normalize offset objects emitted by some callers.  The
            # canonical representation remains dx_mm/dy_mm and is what gets
            # persisted in placement reports.
            if kind == "relative_position":
                offset = item.get("offset_mm", item.get("offset"))
                if isinstance(offset, dict):
                    item.setdefault("dx_mm", offset.get("x", offset.get("dx", 0.0)))
                    item.setdefault("dy_mm", offset.get("y", offset.get("dy", 0.0)))
            endpoints = (item["a"], item["b"])
        else:
            target = item.get("target", item.get("component", item.get("ref")))
            item["target"] = _endpoint(target)
            endpoints = (item["target"],)

        for endpoint in endpoints:
            ref = endpoint["ref"]
            if not ref or ref not in known_refs:
                raise ValueError(f"{item['id']} names unknown component reference {ref!r}")
            selector = str(endpoint.get("geometry") or "component").lower()
            if selector not in {"component", "courtyard", "body", "pad", "centroid"}:
                raise ValueError(f"{item['id']} has unsupported geometry selector {selector!r}")
            endpoint["geometry"] = selector
            meta = footprint_metadata.get(ref) or {}
            geom = meta.get("geometry") or {}
            if selector == "pad" or endpoint.get("pad") is not None:
                pad_number = str(endpoint.get("pad") or "")
                if not pad_number:
                    raise ValueError(f"{item['id']} requires a pad number for {ref}")
                if pad_number not in (geom.get("pads") or {}):
                    source = meta.get("dimension_source", "unknown")
                    raise ValueError(
                        f"{item['id']} cannot resolve {ref} pad {pad_number}; "
                        f"footprint geometry source is {source}"
                    )
            if selector == "courtyard" and not geom.get("courtyard"):
                raise ValueError(f"{item['id']} requires a resolved courtyard for {ref}")

        if kind in PAIR_MIN_TYPES:
            item["min_mm"] = _number(
                item.get("min_mm", item.get("distance_mm", 0.0)),
                f"{item['id']}.min_mm", minimum=0.0,
            )
        elif kind in PAIR_MAX_TYPES:
            item["max_mm"] = _number(
                item.get("max_mm", item.get("distance_mm")),
                f"{item['id']}.max_mm", minimum=0.0,
            )
        elif kind == "align":
            axis = str(item.get("axis", "x")).lower()
            if axis not in {"x", "y", "both"}:
                raise ValueError(f"{item['id']}.axis must be x, y, or both")
            item["axis"] = axis
            item["tolerance_mm"] = _number(item.get("tolerance_mm", 0.25),
                                                f"{item['id']}.tolerance_mm", minimum=0.0)
        elif kind == "relative_position":
            item["dx_mm"] = _number(item.get("dx_mm", 0.0), f"{item['id']}.dx_mm")
            item["dy_mm"] = _number(item.get("dy_mm", 0.0), f"{item['id']}.dy_mm")
            item["tolerance_mm"] = _number(item.get("tolerance_mm", 0.25),
                                                f"{item['id']}.tolerance_mm", minimum=0.0)
        elif kind in {"required_side", "preferred_side"}:
            side = str(item.get("side") or "").lower()
            if side not in {"top", "bottom"}:
                raise ValueError(f"{item['id']}.side must be top or bottom")
            if side == "bottom" and not allow_bottom:
                raise ValueError(f"{item['id']} requires bottom placement but allow_bottom=false")
            item["side"] = side
        elif kind == "orientation":
            item["rotation"] = _number(item.get("rotation"), f"{item['id']}.rotation") % 360.0
            item["tolerance_deg"] = _number(item.get("tolerance_deg", 0.1),
                                                 f"{item['id']}.tolerance_deg", minimum=0.0)
        elif kind == "board_edge_distance":
            item["min_mm"] = _number(item.get("min_mm", 0.0),
                                          f"{item['id']}.min_mm", minimum=0.0)
            if item.get("max_mm") is not None:
                item["max_mm"] = _number(item["max_mm"], f"{item['id']}.max_mm", minimum=0.0)
        elif kind in {"inside_region", "outside_region"}:
            region = item.get("region") or {}
            needed = {"x1", "y1", "x2", "y2"}
            if not needed.issubset(region):
                raise ValueError(f"{item['id']}.region requires x1, y1, x2, y2")
            item["region"] = {key: _number(region[key], f"{item['id']}.region.{key}") for key in needed}
        elif kind == "fixed_position":
            item["x"] = _number(item.get("x"), f"{item['id']}.x")
            item["y"] = _number(item.get("y"), f"{item['id']}.y")
            item["tolerance_mm"] = _number(item.get("tolerance_mm", 0.05),
                                                f"{item['id']}.tolerance_mm", minimum=0.0)

        if kind in PAIR_TYPES:
            item["same_side"] = bool(item.get("same_side", False))
        normalized.append(item)
    return normalized


def _polygon_list(raw_polygons):
    shapes = []
    for raw in raw_polygons or []:
        if len(raw) < 3:
            continue
        poly = Polygon([(float(p[0]), float(p[1])) for p in raw])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_empty:
            shapes.append(poly)
    return shapes


def _pad_local_shape(pad):
    polygon_shapes = _polygon_list(pad.get("polygons"))
    if polygon_shapes:
        return unary_union(polygon_shapes)
    cx, cy = (float(v) for v in pad.get("center", (0.0, 0.0)))
    width, height = (abs(float(v)) for v in pad.get("size", (0.0, 0.0)))
    width = max(width, 1e-6)
    height = max(height, 1e-6)
    shape = str(pad.get("shape", "rect")).lower()
    if shape == "circle":
        result = Point(0.0, 0.0).buffer(max(width, height) / 2.0, quad_segs=16)
    elif shape in {"oval", "capsule"}:
        if width >= height:
            half_line = max((width - height) / 2.0, 0.0)
            result = LineString([(-half_line, 0.0), (half_line, 0.0)]).buffer(
                height / 2.0, quad_segs=12, cap_style="round")
        else:
            half_line = max((height - width) / 2.0, 0.0)
            result = LineString([(0.0, -half_line), (0.0, half_line)]).buffer(
                width / 2.0, quad_segs=12, cap_style="round")
    else:
        result = box(-width / 2.0, -height / 2.0, width / 2.0, height / 2.0)
    result = affinity.rotate(result, float(pad.get("rotation", 0.0)), origin=(0, 0))
    return affinity.translate(result, cx, cy)


def _component_transform(shape, pose):
    x, y, rotation, side = extract_pose_with_side(pose)
    if side == "bottom":
        shape = affinity.scale(shape, xfact=-1.0, yfact=1.0, origin=(0, 0))
    # The native KiCad exporter negates placement JSON rotation. Exported pad
    # coordinates follow that same sign in the board's Y-up frame.
    shape = affinity.rotate(shape, -rotation, origin=(0, 0))
    return affinity.translate(shape, xoff=x, yoff=y)


def component_shape(ref, pose, footprint_metadata, sizes, selector="component", pad=None):
    """Return a Shapely shape in board coordinates for one endpoint."""
    selector = str(selector or "component").lower()
    if selector == "centroid":
        x, y, _ = extract_pose(pose)
        return Point(x, y)
    meta = footprint_metadata.get(ref) or {}
    geom = meta.get("geometry") or {}
    if selector == "pad" or pad is not None:
        pad_items = (geom.get("pads") or {}).get(str(pad), [])
        if not pad_items:
            raise ValueError(f"No pad geometry for {ref} pad {pad}")
        local = unary_union([_pad_local_shape(item) for item in pad_items])
        return _component_transform(local, pose)

    raw = None
    if selector in {"component", "courtyard"}:
        raw = geom.get("courtyard")
    if not raw and selector in {"component", "body"}:
        raw = geom.get("body")
    shapes = _polygon_list(raw)
    if shapes:
        return _component_transform(unary_union(shapes), pose)

    width, height = coerce_size(sizes.get(ref, (3.0, 3.0)))
    return _component_transform(box(-width / 2.0, -height / 2.0,
                                    width / 2.0, height / 2.0), pose)


def _endpoint_shape(endpoint, placement, metadata, sizes, absolute_geometry=None):
    ref = endpoint["ref"]
    selector = endpoint.get("geometry", "component")
    pad = endpoint.get("pad")
    if absolute_geometry is not None:
        entry = absolute_geometry.get(ref) or {}
        if selector == "pad" or pad is not None:
            pads = (entry.get("pads") or {}).get(str(pad), [])
            if not pads:
                raise ValueError(f"No exported pad geometry for {ref} pad {pad}")
            return unary_union(pads)
        shape = entry.get(selector)
        if shape is None and selector == "component":
            shape = entry.get("courtyard") or entry.get("body")
        if shape is not None:
            return shape
    return component_shape(ref, placement[ref], metadata, sizes, selector, pad)


def _pose_point(endpoint, placement, metadata, sizes, absolute_geometry=None):
    shape = _endpoint_shape(endpoint, placement, metadata, sizes, absolute_geometry)
    point = shape.centroid
    return point.x, point.y


def _angle_delta(a, b):
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def evaluate_constraints(placement, constraints, footprint_metadata, sizes,
                         board_w, board_h, *, absolute_geometry=None):
    """Measure constraints and return JSON-serializable result dictionaries."""
    reports = []
    for item in constraints or []:
        kind = item["type"]
        violation = 0.0
        actual = None
        required = None
        measurement = ""
        refs = []

        if kind in PAIR_TYPES:
            a, b = item["a"], item["b"]
            refs = [a["ref"], b["ref"]]
            ax, ay = _pose_point(a, placement, footprint_metadata, sizes, absolute_geometry)
            bx, by = _pose_point(b, placement, footprint_metadata, sizes, absolute_geometry)
            if kind in PAIR_MIN_TYPES | PAIR_MAX_TYPES:
                shape_a = _endpoint_shape(a, placement, footprint_metadata, sizes, absolute_geometry)
                shape_b = _endpoint_shape(b, placement, footprint_metadata, sizes, absolute_geometry)
                distance = float(shape_a.distance(shape_b))
                intersection = shape_a.intersection(shape_b)
                if not intersection.is_empty and intersection.area > 1e-12:
                    distance = -math.sqrt(float(intersection.area))
                actual = distance
                measurement = "edge_to_edge_mm"
                if kind in PAIR_MIN_TYPES:
                    required = {"min_mm": item["min_mm"]}
                    violation = max(item["min_mm"] - distance, 0.0)
                else:
                    required = {"max_mm": item["max_mm"]}
                    violation = max(distance - item["max_mm"], 0.0)
            elif kind == "align":
                deltas = []
                if item["axis"] in {"x", "both"}:
                    deltas.append(abs(ax - bx))
                if item["axis"] in {"y", "both"}:
                    deltas.append(abs(ay - by))
                actual = max(deltas) if deltas else 0.0
                required = {"tolerance_mm": item["tolerance_mm"], "axis": item["axis"]}
                measurement = "alignment_error_mm"
                violation = max(actual - item["tolerance_mm"], 0.0)
            else:  # relative_position
                error = math.hypot((ax - bx) - item["dx_mm"], (ay - by) - item["dy_mm"])
                actual = error
                required = {"dx_mm": item["dx_mm"], "dy_mm": item["dy_mm"],
                            "tolerance_mm": item["tolerance_mm"]}
                measurement = "relative_position_error_mm"
                violation = max(error - item["tolerance_mm"], 0.0)

            if item.get("same_side"):
                side_a = extract_pose_with_side(placement[a["ref"]])[3]
                side_b = extract_pose_with_side(placement[b["ref"]])[3]
                if side_a != side_b:
                    violation += 1.0

        else:
            endpoint = item["target"]
            ref = endpoint["ref"]
            refs = [ref]
            x, y, rotation, side = extract_pose_with_side(placement[ref])
            if kind in {"required_side", "preferred_side"}:
                actual = side
                required = {"side": item["side"]}
                measurement = "board_side"
                violation = 0.0 if side == item["side"] else 1.0
            elif kind == "orientation":
                actual = rotation % 360.0
                required = {"rotation": item["rotation"], "tolerance_deg": item["tolerance_deg"]}
                measurement = "orientation_error_deg"
                violation = max(_angle_delta(actual, item["rotation"]) - item["tolerance_deg"], 0.0)
            elif kind == "fixed_position":
                actual = math.hypot(x - item["x"], y - item["y"])
                required = {"x": item["x"], "y": item["y"],
                            "tolerance_mm": item["tolerance_mm"]}
                measurement = "position_error_mm"
                violation = max(actual - item["tolerance_mm"], 0.0)
            else:
                shape = _endpoint_shape(endpoint, placement, footprint_metadata, sizes,
                                        absolute_geometry)
                if kind == "board_edge_distance":
                    left, bottom, right, top = shape.bounds
                    distance = min(left, bottom, board_w - right, board_h - top)
                    actual = distance
                    required = {"min_mm": item["min_mm"]}
                    violation = max(item["min_mm"] - distance, 0.0)
                    if item.get("max_mm") is not None:
                        required["max_mm"] = item["max_mm"]
                        violation += max(distance - item["max_mm"], 0.0)
                    measurement = "edge_to_board_edge_mm"
                else:
                    region = item["region"]
                    region_shape = box(min(region["x1"], region["x2"]),
                                       min(region["y1"], region["y2"]),
                                       max(region["x1"], region["x2"]),
                                       max(region["y1"], region["y2"]))
                    if kind == "inside_region":
                        outside_area = float(shape.difference(region_shape).area)
                        actual = outside_area
                        violation = math.sqrt(outside_area) if outside_area > 0.0 else 0.0
                        measurement = "area_outside_region_mm2"
                    else:
                        overlap_area = float(shape.intersection(region_shape).area)
                        actual = overlap_area
                        violation = math.sqrt(overlap_area) if overlap_area > 0.0 else 0.0
                        measurement = "area_inside_forbidden_region_mm2"
                    required = {"region": region}

        reports.append({
            "id": item["id"], "type": kind, "hard": item["hard"],
            "weight": item["weight"], "refs": refs,
            "satisfied": violation <= 1e-9,
            "actual": round(actual, 6) if isinstance(actual, float) else actual,
            "required": required, "measurement": measurement,
            "violation": round(float(violation), 6),
        })
    return reports


def constraint_penalty(reports):
    """Convert reports into a GA cost while preserving a useful gradient."""
    total = 0.0
    for report in reports:
        violation = float(report.get("violation", 0.0))
        if violation <= 0.0:
            continue
        weight = float(report.get("weight", 1.0))
        if report.get("hard", True):
            total += 1.0e8 + violation * 1.0e6 * weight
        else:
            total += violation * 100.0 * weight
    return total


def seed_constraints(placement, rotations, sides, constraints, footprint_metadata,
                     sizes, board_w, board_h, *, fixed_refs=None,
                     outline_clearance=0.0):
    """Return an opt-in seed closer to hard relative constraints.

    The first endpoint is moved by default; if it is fixed, the second endpoint
    is moved.  ``move_ref`` can explicitly select either endpoint.  Candidate
    right-angle rotations are evaluated for pad/geometry proximity constraints.
    """
    result = {ref: list(extract_pose(pose)[:2]) for ref, pose in placement.items()}
    rotations = dict(rotations or {})
    sides = dict(sides or {})
    fixed_refs = set(fixed_refs or set())
    changes = []
    locked_rotations = set()
    locked_positions = set()

    def merged_placement():
        return {
            ref: {"x": xy[0], "y": xy[1], "rotation": rotations.get(ref, 0.0),
                  "side": sides.get(ref, "top")}
            for ref, xy in result.items()
        }

    # Apply absolute hard locks first so list order cannot let a later relative
    # seed undo an explicit fixed position or orientation.
    for item in constraints or []:
        if not item.get("hard", True):
            continue
        if item["type"] == "orientation":
            ref = item["target"]["ref"]
            if ref not in fixed_refs:
                rotations[ref] = item["rotation"]
                locked_rotations.add(ref)
                changes.append(f"{item['id']}: set {ref} rotation={item['rotation']:.1f}")
        elif item["type"] == "fixed_position":
            ref = item["target"]["ref"]
            if ref not in fixed_refs and ref in result:
                result[ref] = [item["x"], item["y"]]
                locked_positions.add(ref)
                changes.append(f"{item['id']}: seeded {ref} at ({item['x']:.2f}, {item['y']:.2f})")

    immovable_refs = fixed_refs | locked_positions
    for item in constraints or []:
        if not item.get("hard", True):
            continue
        kind = item["type"]
        if kind not in PAIR_MIN_TYPES | PAIR_MAX_TYPES | {"relative_position", "align"}:
            continue

        a_ref, b_ref = item["a"]["ref"], item["b"]["ref"]
        requested_move = str(item.get("move_ref") or a_ref)
        move_ref = requested_move
        if move_ref in immovable_refs or move_ref not in result:
            move_ref = b_ref if b_ref not in immovable_refs and b_ref in result else ""
        if not move_ref:
            continue
        other_ref = b_ref if move_ref == a_ref else a_ref
        if other_ref not in result:
            continue

        if kind in PAIR_MAX_TYPES and move_ref == a_ref and move_ref not in locked_rotations:
            current = merged_placement()
            best_rotation = rotations.get(move_ref, 0.0)
            best_distance = float("inf")
            for candidate in (0.0, 90.0, 180.0, 270.0):
                rotations[move_ref] = candidate
                current = merged_placement()
                try:
                    sa = _endpoint_shape(item["a"], current, footprint_metadata, sizes)
                    sb = _endpoint_shape(item["b"], current, footprint_metadata, sizes)
                    distance = sa.distance(sb)
                except ValueError:
                    distance = float("inf")
                if distance < best_distance:
                    best_distance, best_rotation = distance, candidate
            rotations[move_ref] = best_rotation

        current = merged_placement()
        if kind == "relative_position":
            bx, by = _pose_point(item["b"], current, footprint_metadata, sizes)
            ax, ay = _pose_point(item["a"], current, footprint_metadata, sizes)
            dx = bx + item["dx_mm"] - ax
            dy = by + item["dy_mm"] - ay
            if move_ref == b_ref:
                dx, dy = -dx, -dy
        elif kind == "align":
            ax, ay = _pose_point(item["a"], current, footprint_metadata, sizes)
            bx, by = _pose_point(item["b"], current, footprint_metadata, sizes)
            dx = (bx - ax) if item["axis"] in {"x", "both"} else 0.0
            dy = (by - ay) if item["axis"] in {"y", "both"} else 0.0
            if move_ref == b_ref:
                dx, dy = -dx, -dy
        else:
            moving_endpoint = item["a"] if move_ref == a_ref else item["b"]
            other_endpoint = item["b"] if move_ref == a_ref else item["a"]
            moving_shape = _endpoint_shape(moving_endpoint, current, footprint_metadata, sizes)
            other_shape = _endpoint_shape(other_endpoint, current, footprint_metadata, sizes)
            mx, my = moving_shape.centroid.x, moving_shape.centroid.y
            ox, oy = other_shape.centroid.x, other_shape.centroid.y
            vx, vy = ox - mx, oy - my
            norm = math.hypot(vx, vy)
            if norm < 1e-9:
                vx, vy, norm = 1.0, 0.0, 1.0
            ux, uy = vx / norm, vy / norm
            distance = moving_shape.distance(other_shape)
            if kind in PAIR_MAX_TYPES:
                shift = max(distance - item["max_mm"] * 0.5, 0.0)
                dx, dy = ux * shift, uy * shift
            else:
                shift = max(item["min_mm"] - distance + 0.1, 0.0)
                dx, dy = -ux * shift, -uy * shift

        result[move_ref][0] += dx
        result[move_ref][1] += dy
        width, height = coerce_size(sizes.get(move_ref, (3.0, 3.0)))
        margin = float(outline_clearance)
        result[move_ref][0] = min(max(result[move_ref][0], width / 2.0 + margin),
                                  board_w - width / 2.0 - margin)
        result[move_ref][1] = min(max(result[move_ref][1], height / 2.0 + margin),
                                  board_h - height / 2.0 - margin)
        changes.append(f"{item['id']}: seeded {move_ref} relative to {other_ref}")
    return result, rotations, sides, changes


def failed_hard_constraints(reports):
    return [report for report in reports if report.get("hard") and not report.get("satisfied")]


def _pcbnew_pad_shape_name(pcbnew, pad):
    value = pad.GetShape()
    mapping = {
        getattr(pcbnew, "PAD_SHAPE_CIRCLE", object()): "circle",
        getattr(pcbnew, "PAD_SHAPE_OVAL", object()): "oval",
        getattr(pcbnew, "PAD_SHAPE_ROUNDRECT", object()): "roundrect",
    }
    return mapping.get(value, "rect")


def extract_exported_board_geometry(board_path):
    """Read actual, transformed KiCad courtyards/pads for the post-export gate."""
    pcbnew = import_pcbnew()

    board = pcbnew.LoadBoard(str(board_path))
    bbox_board = board.GetBoardEdgesBoundingBox()
    board_w = bbox_board.GetWidth() / 1e6
    board_h = bbox_board.GetHeight() / 1e6
    board_left = bbox_board.GetLeft() / 1e6
    board_bottom = bbox_board.GetBottom() / 1e6

    def board_point(point):
        return (point.x / 1e6 - board_left, board_bottom - point.y / 1e6)
    absolute = {}
    poses = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        side = "bottom" if fp.GetLayer() == pcbnew.B_Cu else "top"
        courtyard_layer = pcbnew.B_CrtYd if side == "bottom" else pcbnew.F_CrtYd
        courtyard_shapes = []
        try:
            polyset = fp.GetCourtyard(courtyard_layer)
            for outline_index in range(polyset.OutlineCount()):
                outline = polyset.Outline(outline_index)
                points = [
                    board_point(outline.CPoint(i))
                    for i in range(outline.PointCount())
                ]
                if len(points) >= 3:
                    courtyard_shapes.append(Polygon(points).buffer(0))
        except Exception:
            courtyard_shapes = []

        fp_bbox = fp.GetBoundingBox()
        left = fp_bbox.GetLeft() / 1e6 - board_left
        right = fp_bbox.GetRight() / 1e6 - board_left
        bottom = board_bottom - fp_bbox.GetBottom() / 1e6
        top = board_bottom - fp_bbox.GetTop() / 1e6
        body = box(min(left, right), min(bottom, top), max(left, right), max(bottom, top))
        courtyard = unary_union(courtyard_shapes) if courtyard_shapes else body
        pads = {}
        for pad in fp.Pads():
            pos = pad.GetPosition()
            size = pad.GetSize()
            pad_shape = None
            try:
                copper_layer = pcbnew.F_Cu if pad.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu
                effective = pad.GetEffectiveShape(copper_layer)
                polyset = pcbnew.SHAPE_POLY_SET()
                effective.TransformToPolygon(polyset, 1000, pcbnew.ERROR_OUTSIDE)
                polygons = []
                for outline_index in range(polyset.OutlineCount()):
                    outline = polyset.Outline(outline_index)
                    points = [board_point(outline.CPoint(i)) for i in range(outline.PointCount())]
                    if len(points) >= 3:
                        polygons.append(Polygon(points).buffer(0))
                if polygons:
                    pad_shape = unary_union(polygons)
            except Exception:
                pad_shape = None
            if pad_shape is None:
                raw = {
                    "center": board_point(pos),
                    "size": (size.x / 1e6, size.y / 1e6),
                    "rotation": -float(pad.GetOrientationDegrees()),
                    "shape": _pcbnew_pad_shape_name(pcbnew, pad),
                }
                pad_shape = _pad_local_shape(raw)
            pads.setdefault(str(pad.GetNumber()), []).append(pad_shape)
        absolute[ref] = {"courtyard": courtyard, "component": courtyard,
                         "body": body, "pads": pads}
        centre = courtyard.centroid
        poses[ref] = {
            "x": centre.x, "y": centre.y,
            "rotation": (-float(fp.GetOrientationDegrees())) % 360.0,
            "side": side,
        }
    return board, poses, absolute, board_w, board_h
