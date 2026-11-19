#!/usr/bin/env python3
"""
pcb_auto_placement/cost_functions.py

Evaluates placement quality using multiple cost metrics:
  - HPWL (Half-Perimeter Wire Length)
  - Overlap penalty
  - Boundary violation penalty
  - Keepout/hole violation penalty
"""

from geometry_utils import bbox_overlap, component_bbox, extract_pose, hole_bbox, keepout_bbox
from placement_constraints import constraint_penalty, evaluate_constraints


def compute_hpwl(placement: dict, G) -> float:
    """
    Compute total Half-Perimeter Wire Length (HPWL).
    For each net (edge), HPWL = (max_x - min_x) + (max_y - min_y).
    """
    total_hpwl = 0.0

    for u, v, data in G.edges(data=True):
        if u in placement and v in placement:
            x1, y1, _ = extract_pose(placement[u])
            x2, y2, _ = extract_pose(placement[v])
            weight = data.get("weight", 1)
            hpwl = (abs(x2 - x1) + abs(y2 - y1)) * weight
            total_hpwl += hpwl

    return total_hpwl


def compute_overlap_penalty(placement: dict, sizes: dict, clearance: float = None,
                            sides: dict = None, tht_refs: set = None) -> float:
    """
    Compute total overlap penalty between all component pairs.
    Uses axis-aligned bounding box intersection area.

    Side-aware mode (sides is not None): two SMD parts on opposite sides may
    legally overlap in XY, so their pair penalty is skipped. Pairs involving a
    through-hole part are always penalized — THT pins pass through the board,
    so the part occupies both sides at its XY footprint. sides=None keeps the
    legacy side-blind behavior exactly.
    """
    penalty = 0.0
    clearance = clearance or 0.0
    refs = list(placement.keys())
    tht_refs = tht_refs or set()

    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            r1, r2 = refs[i], refs[j]
            if sides is not None and r1 not in tht_refs and r2 not in tht_refs:
                if sides.get(r1, "top") != sides.get(r2, "top"):
                    continue
            x1, y1, rot1 = extract_pose(placement[r1])
            x2, y2, rot2 = extract_pose(placement[r2])
            box1 = component_bbox(
                x1, y1, sizes.get(r1, (3.0, 3.0)),
                rotation=rot1, clearance=clearance / 2.0
            )
            box2 = component_bbox(
                x2, y2, sizes.get(r2, (3.0, 3.0)),
                rotation=rot2, clearance=clearance / 2.0
            )
            overlap_x, overlap_y = bbox_overlap(box1, box2)

            if overlap_x > 0 and overlap_y > 0:
                penalty += overlap_x * overlap_y * 100

    return penalty


def compute_boundary_penalty(placement: dict, sizes: dict, board_w: float, board_h: float,
                             outline_clearance: float = None,
                             edge_keepout_margin: float = 0.0) -> float:
    """
    Penalty for components extending outside board boundaries (using DRC outline_clearance).
    """
    penalty = 0.0
    oc = (outline_clearance if outline_clearance is not None else 0.0) + max(float(edge_keepout_margin or 0.0), 0.0)

    for ref, pose in placement.items():
        x, y, rot = extract_pose(pose)
        left, bottom, right, top = component_bbox(x, y, sizes.get(ref, (3.0, 3.0)), rotation=rot)

        if left < oc:
            penalty += abs(oc - left) * 50
        if right > board_w - oc:
            penalty += (right - (board_w - oc)) * 50
        if bottom < oc:
            penalty += abs(oc - bottom) * 50
        if top > board_h - oc:
            penalty += (top - (board_h - oc)) * 50

    return penalty


def compute_keepout_penalty(placement: dict, sizes: dict, holes: list, keepouts: list,
                            hole_clearance: float = None) -> float:
    """
    Penalty for components overlapping mounting holes or keepout zones.
    """
    penalty = 0.0
    hole_clearance = max(float(hole_clearance or 0.0), 1.0)

    for ref, pose in placement.items():
        x, y, rot = extract_pose(pose)
        box = component_bbox(x, y, sizes.get(ref, (3.0, 3.0)), rotation=rot)

        for hole in holes:
            overlap_x, overlap_y = bbox_overlap(box, hole_bbox(hole, clearance=hole_clearance))
            if overlap_x > 0 and overlap_y > 0:
                penalty += overlap_x * overlap_y * 250

        for ko in keepouts:
            ko_box = keepout_bbox(ko)
            if ko_box is None:
                continue
            overlap_x, overlap_y = bbox_overlap(box, ko_box)
            if overlap_x > 0 and overlap_y > 0:
                penalty += overlap_x * overlap_y * 200

    return penalty


def compute_congestion_penalty(placement: dict, sizes: dict,
                               board_w: float, board_h: float,
                               grid_cells: int = 5,
                               sides: dict = None) -> float:
    """
    Improvement #7: Routing congestion penalty.
    Divides the board into a grid and penalizes cells with too many components.
    High density in a single cell means routing congestion.

    Side-aware mode (sides is not None): cells are tracked per side — a pile-up
    of same-side parts is what placement congestion is, while parts collocated
    on opposite sides do not conflict (same rule as the overlap penalty).
    """
    cell_w = board_w / grid_cells
    cell_h = board_h / grid_cells
    grid = {}

    for ref, pose in placement.items():
        x, y, _ = extract_pose(pose)
        col = min(int(x / cell_w), grid_cells - 1)
        row = min(int(y / cell_h), grid_cells - 1)
        key = (row, col, sides.get(ref, "top")) if sides is not None else (row, col)
        grid[key] = grid.get(key, 0) + 1

    penalty = 0.0
    for count in grid.values():
        if count > 2:
            penalty += (count - 2) ** 2 * 20
    return penalty


def compute_edge_packing_penalty(placement: dict, sizes: dict,
                                 board_w: float, board_h: float,
                                 margin: float = 0.0) -> float:
    """
    Improvement #8: Edge packing penalty.
    Penalizes non-connector components that are too close to board edges.
    Connectors should be at edges, ICs/passives should not pack against edges.
    """
    penalty = 0.0

    for ref, pose in placement.items():
        x, y, rot = extract_pose(pose)
        left, bottom, right, top = component_bbox(x, y, sizes.get(ref, (3.0, 3.0)), rotation=rot)

        dist_left = left
        dist_right = board_w - right
        dist_bottom = bottom
        dist_top = board_h - top

        min_edge_dist = min(dist_left, dist_right, dist_bottom, dist_top)

        if min_edge_dist < margin:
            penalty += (margin - min_edge_dist) * 5

    return penalty


def compute_bottom_bias_penalty(sides: dict, tht_refs: set = None,
                                weight: float = 1.0,
                                per_ref_weight: dict = None) -> float:
    """
    Regularizer: each part placed on the bottom adds `weight` to the cost so
    the top side stays preferred unless a flip buys real packing/wire gain.
    Through-hole refs are never counted (policy keeps them on top anyway).

    per_ref_weight maps ref -> multiplier: LLM prefer_top hints raise a ref's
    effective bias so it moves only under geometric duress. Missing refs use
    multiplier 1.0, so an empty/absent map keeps legacy numerics exactly.
    """
    if not sides:
        return 0.0
    tht_refs = tht_refs or set()
    per_ref_weight = per_ref_weight or {}
    total = 0.0
    for ref, side in sides.items():
        if side == "bottom" and ref not in tht_refs:
            total += float(weight) * float(per_ref_weight.get(ref, 1.0))
    return total


def compute_block_separation_penalty(placement: dict, block_pairs: list = None,
                                     min_distance: float = 5.0) -> float:
    """
    LLM separate hints: penalize block centroid pairs closer than
    min_distance (mm). block_pairs is a list of (refs_a, refs_b) ref-set
    tuples (e.g. power vs analog-front-end); the penalty is linear in the
    shortfall so the GA can gradient-walk the blocks apart. Empty/absent
    pairs return exactly 0.0 (default-off identity).
    """
    if not block_pairs:
        return 0.0
    penalty = 0.0
    for refs_a, refs_b in block_pairs:
        pts_a = [
            (extract_pose(placement[ref])[0], extract_pose(placement[ref])[1])
            for ref in refs_a if ref in placement
        ]
        pts_b = [
            (extract_pose(placement[ref])[0], extract_pose(placement[ref])[1])
            for ref in refs_b if ref in placement
        ]
        if not pts_a or not pts_b:
            continue
        ca = (sum(p[0] for p in pts_a) / len(pts_a), sum(p[1] for p in pts_a) / len(pts_a))
        cb = (sum(p[0] for p in pts_b) / len(pts_b), sum(p[1] for p in pts_b) / len(pts_b))
        dist = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5
        if dist < min_distance:
            penalty += min_distance - dist
    return penalty


def compute_total_cost(placement: dict, G, sizes: dict,
                       board_w: float, board_h: float,
                       holes: list = None, keepouts: list = None,
                       outline_clearance: float = None,
                       component_clearance: float = None,
                       hole_clearance: float = None,
                       edge_keepout_margin: float = 0.0,
                       sides: dict = None, tht_refs: set = None,
                       bottom_bias: float = 0.0,
                       bottom_bias_weights: dict = None,
                       block_pairs: list = None,
                       block_separation_distance: float = 5.0,
                       relative_constraints: list = None,
                       footprint_metadata: dict = None,
                       constraint_fixed_placement: dict = None) -> float:
    """
    Compute the total weighted cost for a placement.
    All clearance values come from per-board DRC config.

    Side-aware mode (sides is not None): overlap becomes side-aware (see
    compute_overlap_penalty) and a per-bottom-part bias is added. Holes and
    keepouts stay side-independent — drilled features pass through the board,
    so they constrain both sides. sides=None keeps legacy numerics exactly.
    bottom_bias_weights (ref -> multiplier) scales the per-part bias for
    prefer_top hints; only meaningful in side-aware mode.
    block_pairs ((refs_a, refs_b) tuples) adds the LLM separate-hint
    repulsion (compute_block_separation_penalty); None keeps it off.
    """
    holes = holes or []
    keepouts = keepouts or []
    cc = component_clearance if component_clearance is not None else 0.0
    hc = hole_clearance if hole_clearance is not None else max(cc, 1.0)

    hpwl = compute_hpwl(placement, G)
    overlap = compute_overlap_penalty(placement, sizes, clearance=cc,
                                      sides=sides, tht_refs=tht_refs)
    boundary = compute_boundary_penalty(
        placement, sizes, board_w, board_h,
        outline_clearance=outline_clearance,
        edge_keepout_margin=edge_keepout_margin
    )
    keepout = compute_keepout_penalty(placement, sizes, holes, keepouts, hole_clearance=hc)
    congestion = compute_congestion_penalty(placement, sizes, board_w, board_h, sides=sides)
    edge_pack_margin = max(
        float(outline_clearance or 0.0),
        float(edge_keepout_margin or 0.0),
    )
    edge_pack = compute_edge_packing_penalty(
        placement, sizes, board_w, board_h, margin=edge_pack_margin
    )

    # Treat overlap, board-boundary, and keepout/hole violations as hard constraints.
    hard_violation = 0.0
    if overlap > 0.0:
        hard_violation += 1.0e8 + overlap
    if boundary > 0.0:
        hard_violation += 1.0e8 + boundary
    if keepout > 0.0:
        hard_violation += 1.0e8 + keepout

    total = hard_violation + hpwl + overlap + boundary + keepout + congestion + edge_pack + (
        compute_bottom_bias_penalty(sides, tht_refs=tht_refs, weight=bottom_bias,
                                    per_ref_weight=bottom_bias_weights)
        if sides is not None else 0.0
    ) + compute_block_separation_penalty(
        placement, block_pairs, min_distance=block_separation_distance
    )

    # Optional exact geometry/pad constraints.  With no constraints this branch
    # is skipped completely, preserving the legacy centroid/AABB cost numerics.
    if relative_constraints:
        constraint_placement = {}
        for ref, pose in placement.items():
            x, y, rotation = extract_pose(pose)
            constraint_placement[ref] = {
                "x": x, "y": y, "rotation": rotation,
                "side": (sides or {}).get(ref, "top"),
            }
        for ref, pose in (constraint_fixed_placement or {}).items():
            x, y, rotation = extract_pose(pose)
            side = pose.get("side", "top") if isinstance(pose, dict) else "top"
            constraint_placement[ref] = {
                "x": x, "y": y, "rotation": rotation, "side": side,
            }
        reports = evaluate_constraints(
            constraint_placement, relative_constraints,
            footprint_metadata or {}, sizes, board_w, board_h,
        )
        total += constraint_penalty(reports)
    return total
