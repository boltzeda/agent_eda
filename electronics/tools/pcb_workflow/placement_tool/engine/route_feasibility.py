#!/usr/bin/env python3
"""Board-independent routing-feasibility audit and bounded placement repair."""

from __future__ import annotations

import csv
import heapq
import math
import os
from copy import deepcopy

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from placement_constraints import component_shape, evaluate_constraints, failed_hard_constraints


def _rule_for_net(drc, net):
    rule = dict(drc or {})
    for item in rule.get("net_classes") or []:
        if net in (item.get("nets") or []):
            rule.update(item)
            break
    return rule


def _load_connections(input_dir):
    path = os.path.join(input_dir, "net_nodes.csv")
    if not os.path.exists(path):
        return {}
    grouped = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            net, ref, pin = (str(row.get(k) or "").strip() for k in ("net", "ref", "pin"))
            if net and ref and pin:
                grouped.setdefault(net, []).append((ref, pin))
    return grouped


def _pad_shape(ref, pin, placements, footprints, sizes):
    if ref not in placements:
        return None
    try:
        return component_shape(ref, placements[ref], footprints, sizes,
                               selector="pad", pad=pin)
    except (KeyError, ValueError):
        return None


def _escape_available(ref, pin, placements, footprints, sizes, margin):
    target = _pad_shape(ref, pin, placements, footprints, sizes)
    if target is None or target.is_empty:
        return None
    other_pads = []
    for other_pin in ((footprints.get(ref) or {}).get("geometry") or {}).get("pads", {}):
        if str(other_pin) == str(pin):
            continue
        shape = _pad_shape(ref, str(other_pin), placements, footprints, sizes)
        if shape is not None and not shape.is_empty:
            other_pads.append(shape.buffer(margin, join_style=2))
    if not other_pads:
        return True
    blocked = unary_union(other_pads)
    c = target.centroid
    radius = max(target.bounds[2] - target.bounds[0], target.bounds[3] - target.bounds[1]) / 2.0
    reach = radius + margin + 0.15
    for angle in range(0, 360, 30):
        a = math.radians(angle)
        end = (c.x + reach * math.cos(a), c.y + reach * math.sin(a))
        if not LineString([(c.x, c.y), end]).intersects(blocked):
            return True
    return False


def _channel_exists(start, goal, obstacle_shapes, board_keepin):
    line = LineString([start, goal])
    obstacles = unary_union(obstacle_shapes) if obstacle_shapes else None

    def clear(a, b):
        candidate = LineString([a, b])
        if not board_keepin.covers(candidate):
            return False
        return obstacles is None or obstacles.is_empty or not candidate.intersects(obstacles)

    if clear(start, goal):
        return True, line.length

    # Sparse visibility graph around expanded component envelopes.  This is a
    # coarse placement gate, not a second router: exact copper is still handled
    # by LineExplore/FreeRouting.
    points = [start, goal]
    for shape in obstacle_shapes:
        x1, y1, x2, y2 = shape.bounds
        e = 0.03
        points.extend(((x1 - e, y1 - e), (x2 + e, y1 - e),
                       (x2 + e, y2 + e), (x1 - e, y2 + e)))
    points = [p for p in dict.fromkeys((round(p[0], 4), round(p[1], 4)) for p in points)
              if board_keepin.covers(Point(p))]
    if start not in points or goal not in points:
        return False, None
    source, target = points.index(start), points.index(goal)
    queue = [(0.0, source)]
    distances = {source: 0.0}
    visited = set()
    while queue:
        cost, index = heapq.heappop(queue)
        if index in visited:
            continue
        if index == target:
            return True, cost
        visited.add(index)
        p = points[index]
        nearest = sorted(
            ((math.hypot(p[0] - q[0], p[1] - q[1]), j)
             for j, q in enumerate(points) if j != index and j not in visited),
            key=lambda item: item[0],
        )[:32]
        for distance, j in nearest:
            if not clear(p, points[j]):
                continue
            new_cost = cost + distance
            if new_cost < distances.get(j, float("inf")):
                distances[j] = new_cost
                heapq.heappush(queue, (new_cost, j))
    return False, None


def audit_route_feasibility(input_dir, placements, footprints, sizes, board_w, board_h,
                            drc, layer_config=None):
    nets = _load_connections(input_dir)
    zone_nets = set(((layer_config or {}).get("fills") or {}).values())
    component_shapes = {
        ref: component_shape(ref, pose, footprints, sizes, selector="component")
        for ref, pose in placements.items()
    }
    failures, checked, estimated_length = [], 0, 0.0
    for net, endpoints in sorted(nets.items()):
        if net in zone_nets or len(endpoints) < 2:
            continue
        pads = [(ref, pin, _pad_shape(ref, pin, placements, footprints, sizes))
                for ref, pin in endpoints]
        pads = [(ref, pin, shape) for ref, pin, shape in pads if shape is not None]
        if len(pads) < 2:
            continue
        remaining = pads[1:]
        tree = [pads[0]]
        while remaining:
            distance, left, right = min(
                (a[2].centroid.distance(b[2].centroid), a, b)
                for a in tree for b in remaining
            )
            remaining.remove(right)
            tree.append(right)
            checked += 1
            rule = _rule_for_net(drc, net)
            margin = float(rule.get("clearance", 0.2)) + float(rule.get("trace_width", 0.25)) / 2.0
            refs = {left[0], right[0]}
            obstacle_shapes = [shape.buffer(margin, join_style=2)
                               for ref, shape in component_shapes.items() if ref not in refs]
            edge = float(rule.get("outline_clearance", 0.5)) + float(rule.get("trace_width", 0.25)) / 2.0
            keepin = box(edge, edge, board_w - edge, board_h - edge)
            escape_left = _escape_available(left[0], left[1], placements, footprints, sizes, margin)
            escape_right = _escape_available(right[0], right[1], placements, footprints, sizes, margin)
            start = (round(left[2].centroid.x, 4), round(left[2].centroid.y, 4))
            goal = (round(right[2].centroid.x, 4), round(right[2].centroid.y, 4))
            channel, path_length = _channel_exists(start, goal, obstacle_shapes, keepin)
            if escape_left is False or escape_right is False or not channel:
                direct = LineString([start, goal])
                blockers = sorted(ref for ref, shape in component_shapes.items()
                                  if ref not in refs and direct.intersects(shape.buffer(margin)))
                failures.append({
                    "net": net,
                    "from": {"ref": left[0], "pad": left[1]},
                    "to": {"ref": right[0], "pad": right[1]},
                    "cause": "pad_escape" if escape_left is False or escape_right is False
                    else "no_coarse_channel",
                    "blockers": blockers,
                })
            else:
                estimated_length += path_length or distance
    return {"checked_connections": checked, "failed_connections": failures,
            "failed_count": len(failures),
            "estimated_route_length_mm": round(estimated_length, 3)}


def repair_route_feasibility(input_dir, placements, footprints, sizes, board_w, board_h,
                             drc, layer_config=None, fixed_refs=None,
                             relative_constraints=None, max_components=4):
    """Try local endpoint moves/rotations and commit only strict improvements."""
    current = deepcopy(placements)
    fixed_refs = set(fixed_refs or ())
    baseline = audit_route_feasibility(input_dir, current, footprints, sizes, board_w,
                                       board_h, drc, layer_config)
    initial = deepcopy(baseline)
    changed = []
    implicated = []
    for failure in baseline["failed_connections"]:
        for endpoint in (failure["from"], failure["to"]):
            ref = endpoint["ref"]
            if ref not in fixed_refs and ref not in implicated:
                implicated.append(ref)
    for ref in implicated[:max_components]:
        pose = current[ref]
        x, y = float(pose["x"]), float(pose["y"])
        rotation = float(pose.get("rotation", 0.0))
        side = str(pose.get("side", "top"))
        candidates = []
        for rot in (rotation, rotation + 90, rotation + 180, rotation + 270):
            candidates.append((x, y, rot % 360.0))
        for step in (0.75, 1.5, 3.0):
            for dx, dy in ((step, 0), (-step, 0), (0, step), (0, -step),
                           (step, step), (step, -step), (-step, step), (-step, -step)):
                candidates.append((x + dx, y + dy, rotation))
        best = None
        for cx, cy, crot in candidates[:16]:
            trial = deepcopy(current)
            trial[ref] = {**pose, "x": cx, "y": cy, "rotation": crot, "side": side}
            shape = component_shape(ref, trial[ref], footprints, sizes, selector="component")
            if not box(0, 0, board_w, board_h).covers(shape):
                continue
            collision = False
            for other_ref, other_pose in trial.items():
                if other_ref == ref or str(other_pose.get("side", "top")) != side:
                    continue
                other = component_shape(other_ref, other_pose, footprints, sizes,
                                        selector="component")
                if shape.intersection(other).area > 1e-6:
                    collision = True
                    break
            if collision:
                continue
            if relative_constraints:
                constraint_report = evaluate_constraints(
                    trial, relative_constraints, footprints, sizes, board_w, board_h)
                if failed_hard_constraints(constraint_report):
                    continue
            report = audit_route_feasibility(input_dir, trial, footprints, sizes,
                                             board_w, board_h, drc, layer_config)
            score = (report["failed_count"], report["estimated_route_length_mm"])
            if best is None or score < best[0]:
                best = (score, trial, report, (cx, cy, crot))
        current_score = (baseline["failed_count"], baseline["estimated_route_length_mm"])
        if best is not None and best[0] < current_score:
            before = dict(current[ref])
            current, baseline = best[1], best[2]
            changed.append({"ref": ref, "before": before, "after": dict(current[ref])})
    return current, {"initial": initial, "final": baseline, "repairs": changed}
