#!/usr/bin/env python3
"""
Cluster-region placement and local fallback helpers.
"""

import math

import numpy as np

from geometry_utils import (
    bbox_area,
    bbox_center,
    bbox_distance,
    bbox_from_center,
    bbox_overlap,
    clip_bbox,
    coerce_size,
    component_bbox,
    extract_pose,
    hole_bbox,
    keepout_bbox,
)


def _collect_obstacle_boxes(holes, keepouts, placed_regions, fixed_keepouts, component_clearance):
    boxes = []
    for keepout in keepouts or []:
        ko_box = keepout_bbox(keepout)
        if ko_box is not None:
            boxes.append(ko_box)
    for keepout in fixed_keepouts or []:
        ko_box = keepout_bbox(keepout)
        if ko_box is not None:
            boxes.append(ko_box)
    hole_clearance = max(component_clearance, 1.0)
    for hole in holes or []:
        boxes.append(hole_bbox(hole, clearance=hole_clearance))
    for meta in placed_regions.values():
        boxes.append(
            bbox_from_center(meta["x"], meta["y"], meta["w"], meta["h"])
        )
    return boxes


def _candidate_centers(cluster_w, cluster_h, board_w, board_h, outline_clearance, grid_n,
                       edge_keepout_margin=0.0):
    effective_outline = outline_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
    min_x = cluster_w / 2.0 + effective_outline
    max_x = board_w - cluster_w / 2.0 - effective_outline
    min_y = cluster_h / 2.0 + effective_outline
    max_y = board_h - cluster_h / 2.0 - effective_outline

    if min_x > max_x:
        min_x = max_x = board_w / 2.0
    if min_y > max_y:
        min_y = max_y = board_h / 2.0

    xs = np.linspace(min_x, max_x, num=max(1, grid_n))
    ys = np.linspace(min_y, max_y, num=max(1, grid_n))
    return [(float(x), float(y)) for y in ys for x in xs]


def _boundary_overflow(box, board_w, board_h, outline_clearance, edge_keepout_margin=0.0):
    left, bottom, right, top = box
    effective_outline = outline_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
    overflow = 0.0
    overflow += max(effective_outline - left, 0.0)
    overflow += max(effective_outline - bottom, 0.0)
    overflow += max(right - (board_w - effective_outline), 0.0)
    overflow += max(top - (board_h - effective_outline), 0.0)
    return overflow


def _weighted_points_distance(cx, cy, points):
    if not points:
        return None
    total_weight = sum(weight for _, _, weight in points)
    if total_weight <= 0:
        return None
    distance = 0.0
    for px, py, weight in points:
        distance += math.hypot(cx - px, cy - py) * weight
    return distance / total_weight


def _dist_to_preferred_edge(box, preferred_edge, board_w, board_h):
    if not preferred_edge:
        return None
    left, bottom, right, top = box
    if preferred_edge == "left":
        return left
    if preferred_edge == "right":
        return max(board_w - right, 0.0)
    if preferred_edge == "bottom":
        return bottom
    if preferred_edge == "top":
        return max(board_h - top, 0.0)
    return None


def _dead_space_penalty(box, board_w, board_h, occupied_boxes):
    max_dim = max(board_w, board_h, 1.0)
    if not occupied_boxes:
        cx, cy = bbox_center(box)
        edge_gap = min(cx, cy, board_w - cx, board_h - cy)
        return min(1.0, edge_gap / max_dim)
    nearest_gap = min(bbox_distance(box, other) for other in occupied_boxes)
    return min(1.0, nearest_gap / max_dim)


def _box_diag(box):
    width = max(box[2] - box[0], 0.0)
    height = max(box[3] - box[1], 0.0)
    return math.hypot(width, height)


def _cluster_separation_penalty(box, placed_cluster_boxes, board_w, board_h):
    """
    Penalize candidates that sit too close to already placed clusters.
    Returns 0 when the nearest cluster gap is comfortably large.
    """
    if not placed_cluster_boxes:
        return 0.0

    max_dim = max(board_w, board_h, 1.0)
    nearest_gap = float("inf")
    nearest_diag = 0.0
    for other in placed_cluster_boxes:
        gap = bbox_distance(box, other)
        if gap < nearest_gap:
            nearest_gap = gap
            nearest_diag = _box_diag(other)

    desired_gap = min(
        0.22 * max_dim,
        0.10 * max_dim + 0.20 * (_box_diag(box) + nearest_diag),
    )
    if desired_gap <= 0.0:
        return 0.0
    return min(1.0, max(0.0, desired_gap - nearest_gap) / desired_gap)


def _dense_region_penalty(box, placed_cluster_boxes, board_w, board_h):
    """
    Penalize candidates that fall inside an already dense neighborhood.
    Nearby larger clusters contribute more density pressure.
    """
    if not placed_cluster_boxes:
        return 0.0

    board_area = max(board_w * board_h, 1e-6)
    influence_radius = 0.25 * max(board_w, board_h, 1.0)
    density = 0.0
    for other in placed_cluster_boxes:
        gap = bbox_distance(box, other)
        influence = math.exp(-gap / max(influence_radius, 1e-6))
        area_weight = min(1.0, bbox_area(other) / max(0.08 * board_area, 1e-6))
        density += influence * (0.5 + 0.5 * area_weight)
    return min(1.0, density)


def _true_center_penalty(cx, cy, board_w, board_h):
    """
    Stronger center pull than the generic center-distance term.
    """
    center_x = board_w / 2.0
    center_y = board_h / 2.0
    target_radius = max(0.18 * max(board_w, board_h, 1.0), 1e-6)
    return min(1.0, math.hypot(cx - center_x, cy - center_y) / target_radius)


def build_cluster_illegal_mask(
    cluster_w,
    cluster_h,
    board_w,
    board_h,
    holes,
    keepouts,
    fixed_keepouts,
    placed_regions,
    outline_clearance,
    component_clearance,
    edge_keepout_margin=0.0,
    grid_n=48,
):
    """Return an N x N legality mask for a cluster envelope."""
    mask = np.zeros((grid_n, grid_n), dtype=np.float32)
    obstacle_boxes = _collect_obstacle_boxes(
        holes, keepouts, placed_regions, fixed_keepouts, component_clearance
    )
    candidates = _candidate_centers(
        cluster_w, cluster_h, board_w, board_h, outline_clearance, grid_n,
        edge_keepout_margin=edge_keepout_margin
    )
    for idx, (cx, cy) in enumerate(candidates):
        gx = idx % grid_n
        gy = idx // grid_n
        box = bbox_from_center(cx, cy, cluster_w, cluster_h)
        overflow = _boundary_overflow(
            box, board_w, board_h, outline_clearance,
            edge_keepout_margin=edge_keepout_margin
        )
        illegal = overflow > 0.0
        if not illegal:
            for obstacle in obstacle_boxes:
                overlap_x, overlap_y = bbox_overlap(box, obstacle)
                if overlap_x > 0 and overlap_y > 0:
                    illegal = True
                    break
        if illegal:
            mask[gy, gx] = 1.0
    return mask


def build_cluster_preference_map(
    cluster_meta,
    placed_regions,
    cluster_info,
    board_w,
    board_h,
    fixed_connector_positions,
    edge_keepout_margin=0.0,
    grid_n=48,
):
    """Return an N x N soft preference map for candidate cluster centers."""
    pref = np.zeros((grid_n, grid_n), dtype=np.float32)
    candidates = _candidate_centers(
        cluster_meta["bbox_w"],
        cluster_meta["bbox_h"],
        board_w,
        board_h,
        cluster_meta.get("outline_clearance", 0.0),
        grid_n,
        edge_keepout_margin=edge_keepout_margin,
    )
    max_diag = max((board_w ** 2 + board_h ** 2) ** 0.5, 1.0)
    for idx, (cx, cy) in enumerate(candidates):
        gx = idx % grid_n
        gy = idx // grid_n
        box = bbox_from_center(cx, cy, cluster_meta["bbox_w"], cluster_meta["bbox_h"])
        score = 0.0
        edge_dist = _dist_to_preferred_edge(
            box, cluster_meta.get("preferred_edge"), board_w, board_h
        )
        if edge_dist is not None:
            score += 1.0 - min(1.0, edge_dist / max(board_w, board_h, 1.0))

        center_dist = math.hypot(cx - board_w / 2.0, cy - board_h / 2.0) / max_diag
        if cluster_meta["role"] == "center_hub":
            score += 1.0 - center_dist

        connector_points = []
        for ref, weight in cluster_meta.get("connector_links", {}).items():
            if ref in fixed_connector_positions and weight > 0:
                props = fixed_connector_positions[ref]
                connector_points.append((float(props["x"]), float(props["y"]), float(weight)))
        connector_dist = _weighted_points_distance(cx, cy, connector_points)
        if connector_dist is not None:
            score += 1.0 - min(1.0, connector_dist / max(board_w, board_h, 1.0))

        linked_cluster_points = []
        for other_cid, weight in cluster_meta.get("intercluster_links", {}).items():
            other = placed_regions.get(other_cid)
            if other and weight > 0:
                linked_cluster_points.append((other["x"], other["y"], float(weight)))
        linked_dist = _weighted_points_distance(cx, cy, linked_cluster_points)
        if linked_dist is not None:
            score += 1.0 - min(1.0, linked_dist / max(board_w, board_h, 1.0))

        pref[gy, gx] = score
    return pref


def _score_cluster_candidate(
    cluster_meta,
    box,
    board_w,
    board_h,
    fixed_connector_positions,
    placed_regions,
    placed_cluster_boxes,
):
    cx, cy = bbox_center(box)
    max_dim = max(board_w, board_h, 1.0)
    max_diag = max((board_w ** 2 + board_h ** 2) ** 0.5, 1.0)

    dist_to_preferred_edge_norm = 0.0
    edge_dist = _dist_to_preferred_edge(
        box, cluster_meta.get("preferred_edge"), board_w, board_h
    )
    if edge_dist is not None:
        dist_to_preferred_edge_norm = min(1.0, edge_dist / max_dim)

    connector_points = []
    for ref, weight in cluster_meta.get("connector_links", {}).items():
        if ref in fixed_connector_positions and weight > 0:
            props = fixed_connector_positions[ref]
            connector_points.append((float(props["x"]), float(props["y"]), float(weight)))
    connector_dist = _weighted_points_distance(cx, cy, connector_points)
    dist_to_linked_connectors_norm = (
        min(1.0, connector_dist / max_dim) if connector_dist is not None else 0.0
    )

    linked_cluster_points = []
    for other_cid, weight in cluster_meta.get("intercluster_links", {}).items():
        other = placed_regions.get(other_cid)
        if other and weight > 0:
            linked_cluster_points.append((other["x"], other["y"], float(weight)))
    linked_dist = _weighted_points_distance(cx, cy, linked_cluster_points)
    dist_to_connected_clusters_norm = (
        min(1.0, linked_dist / max_dim) if linked_dist is not None else 0.0
    )

    dist_to_board_center_norm = min(
        1.0,
        math.hypot(cx - board_w / 2.0, cy - board_h / 2.0) / max_diag,
    )
    true_center_penalty = _true_center_penalty(cx, cy, board_w, board_h)
    separation_penalty = _cluster_separation_penalty(
        box, placed_cluster_boxes, board_w, board_h
    )
    density_penalty = _dense_region_penalty(
        box, placed_cluster_boxes, board_w, board_h
    )

    role = cluster_meta["role"]
    if role == "center_hub":
        return (
            2.0 * dist_to_connected_clusters_norm
            + 6.5 * true_center_penalty
            + 2.5 * separation_penalty
            + 1.0 * density_penalty
        )
    if role == "edge_hard":
        return (
            4.0 * dist_to_preferred_edge_norm
            + 3.0 * dist_to_linked_connectors_norm
            + 2.5 * dist_to_connected_clusters_norm
            + 2.5 * separation_penalty
            + 1.5 * density_penalty
        )
    if role == "support":
        return (
            2.25 * dist_to_connected_clusters_norm
            + 2.75 * true_center_penalty
            + 4.0 * separation_penalty
            + 4.0 * density_penalty
        )
    return (
        1.75 * dist_to_connected_clusters_norm
        + 2.25 * true_center_penalty
        + 4.5 * separation_penalty
        + 4.5 * density_penalty
    )


def _cluster_scale_candidates(cluster_meta):
    """Candidate envelope growth factors for globally placing a cluster."""
    packing_pressure = min(1.0, max(0.0, float(cluster_meta.get("packing_pressure", 0.0))))
    member_count = max(int(cluster_meta.get("member_count", 0)), 1)

    base_scales = [1.0]
    if member_count >= 3 or packing_pressure >= 0.20:
        base_scales.extend([1.12, 1.25])
    if member_count >= 5 or packing_pressure >= 0.45:
        base_scales.extend([1.40, 1.55])
    if member_count >= 8 or packing_pressure >= 0.70:
        base_scales.append(1.75)

    preferred_scale = 1.0 + 0.45 * packing_pressure
    seen = set()
    scales = []
    for scale in sorted(base_scales + [preferred_scale]):
        rounded = round(scale, 3)
        if rounded not in seen:
            seen.add(rounded)
            scales.append(rounded)
    return scales, preferred_scale


def place_clusters(
    cluster_order,
    cluster_info,
    board_w,
    board_h,
    holes,
    keepouts,
    fixed_keepouts,
    outline_clearance,
    fixed_connector_positions,
    component_clearance,
    edge_keepout_margin=0.0,
    grid_n=48,
):
    """Place cluster envelopes on the board before local component placement."""
    cluster_regions = {}

    for cid in cluster_order:
        cluster_meta = cluster_info[cid]
        obstacle_boxes = _collect_obstacle_boxes(
            holes, keepouts, cluster_regions, fixed_keepouts, component_clearance
        )
        placed_cluster_boxes = [
            bbox_from_center(meta["x"], meta["y"], meta["w"], meta["h"])
            for meta in cluster_regions.values()
        ]

        best_legal = None
        best_legal_cost = float("inf")
        best_soft = None
        best_soft_cost = float("inf")
        scales, preferred_scale = _cluster_scale_candidates(cluster_meta)
        for scale in scales:
            cluster_w = cluster_meta["bbox_w"] * scale
            cluster_h = cluster_meta["bbox_h"] * scale
            candidates = _candidate_centers(
                cluster_w, cluster_h, board_w, board_h, outline_clearance, grid_n,
                edge_keepout_margin=edge_keepout_margin
            )
            size_penalty = abs(scale - preferred_scale)

            for cx, cy in candidates:
                box = bbox_from_center(cx, cy, cluster_w, cluster_h)
                overflow = _boundary_overflow(
                    box, board_w, board_h, outline_clearance,
                    edge_keepout_margin=edge_keepout_margin
                )
                overlap_penalty = 0.0
                for obstacle in obstacle_boxes:
                    overlap_x, overlap_y = bbox_overlap(box, obstacle)
                    if overlap_x > 0 and overlap_y > 0:
                        overlap_penalty += overlap_x * overlap_y

                score = _score_cluster_candidate(
                    cluster_meta,
                    box,
                    board_w,
                    board_h,
                    fixed_connector_positions,
                    cluster_regions,
                    placed_cluster_boxes,
                )
                score += 0.40 * size_penalty
                soft_cost = score + 1000.0 * overlap_penalty + 1000.0 * overflow

                if overlap_penalty <= 0.0 and overflow <= 0.0:
                    if score < best_legal_cost:
                        best_legal_cost = score
                        best_legal = (cx, cy, box, cluster_w, cluster_h, scale)
                elif soft_cost < best_soft_cost:
                    best_soft_cost = soft_cost
                    best_soft = (cx, cy, box, cluster_w, cluster_h, scale)

        chosen = best_legal or best_soft
        if chosen is None:
            cluster_w = cluster_meta["bbox_w"]
            cluster_h = cluster_meta["bbox_h"]
            chosen = (
                board_w / 2.0,
                board_h / 2.0,
                bbox_from_center(board_w / 2.0, board_h / 2.0, cluster_w, cluster_h),
                cluster_w,
                cluster_h,
                1.0,
            )
        cx, cy, box, cluster_w, cluster_h, scale_used = chosen
        cluster_regions[cid] = {
            "x": float(cx),
            "y": float(cy),
            "w": float(cluster_w),
            "h": float(cluster_h),
            "scale": float(scale_used),
            "role": cluster_meta["role"],
            "preferred_edge": cluster_meta.get("preferred_edge"),
            "refs": list(cluster_meta["refs"]),
            "box": box,
        }

    return cluster_regions


def order_cluster_components(cluster_refs, G, sizes, pcb_components):
    """Order components so the most constraining parts are placed first locally."""
    def score(ref):
        node = G.nodes.get(ref, {})
        comp = pcb_components.get(ref)
        semantic_name = getattr(getattr(comp, "semantic_class", None), "name", "")
        interface_name = getattr(getattr(comp, "interface_type", None), "name", "")
        prefix = node.get("prefix", "")
        area = _component_area(ref, sizes)
        value = 0.0
        if semantic_name == "MCU_FPGA":
            value += 500.0
        elif semantic_name == "MEMORY":
            value += 420.0
        elif semantic_name == "ANALOG_IC":
            value += 320.0
        elif semantic_name == "POWER_ENTRY":
            value += 280.0
        elif semantic_name == "USER_INTERFACE":
            value += 180.0
        elif semantic_name == "PASSIVE":
            value -= 120.0

        if prefix == "U":
            value += 240.0
        elif prefix in {"Y", "X"}:
            value += 220.0
        elif prefix in {"R", "C", "L", "D"}:
            value -= 80.0

        if interface_name in {"POWER", "RF", "SAFETY"}:
            value += 120.0
        elif interface_name in {"USER", "IO"}:
            value += 60.0

        value += float(G.degree(ref)) * 12.0
        value += area
        return value

    return sorted(cluster_refs, key=lambda ref: (-score(ref), ref))


def _component_area(ref, sizes):
    width, height = coerce_size(sizes.get(ref, (3.0, 3.0)))
    return width * height


def _expand_region_within_board(region, scale, board_w, board_h,
                                outline_clearance, edge_keepout_margin):
    """Expand a local region while keeping it inside the legal movable interior."""
    effective_outline = outline_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
    max_half_w = max((board_w / 2.0) - effective_outline, region["w"] / 2.0)
    max_half_h = max((board_h / 2.0) - effective_outline, region["h"] / 2.0)
    half_w = min((region["w"] * scale) / 2.0, max_half_w)
    half_h = min((region["h"] * scale) / 2.0, max_half_h)
    cx = min(max(region["x"], effective_outline + half_w), board_w - effective_outline - half_w)
    cy = min(max(region["y"], effective_outline + half_h), board_h - effective_outline - half_h)
    return {"x": cx, "y": cy, "w": 2.0 * half_w, "h": 2.0 * half_h}


def _component_slot_area(ref, sizes, component_clearance):
    width, height = coerce_size(sizes.get(ref, (3.0, 3.0)))
    pad = max(float(component_clearance), 0.8)
    return (width + pad) * (height + pad)


def _fine_candidate_points(region_box, region_cx, region_cy, step=0.25):
    """Dense center-sorted grid used when the coarse grid finds no slot.

    Two large parts plus the boundary walls can leave a legal center window
    narrower than the coarse grid step; a ~0.25 mm raster threads it.
    """
    nx = max(3, int(math.ceil((region_box[2] - region_box[0]) / step)))
    ny = max(3, int(math.ceil((region_box[3] - region_box[1]) / step)))
    xs = np.linspace(region_box[0], region_box[2], num=nx + 2)[1:-1]
    ys = np.linspace(region_box[1], region_box[3], num=ny + 2)[1:-1]
    return sorted(
        ((float(x), float(y)) for y in ys for x in xs),
        key=lambda point: math.hypot(point[0] - region_cx, point[1] - region_cy),
    )


def _try_seed_components_in_region(
    refs,
    region,
    sizes,
    existing_placement,
    holes,
    keepouts,
    component_clearance,
    outline_clearance,
    board_w=None,
    board_h=None,
    edge_keepout_margin=0.0,
    ordered_refs=None,
    allow_bottom=False,
    bottom_refs=None,
    tht_refs=None,
    existing_sides=None,
    forced_bottom_refs=None,
):
    """Try one deterministic seeding pass inside a specific region.

    When allow_bottom is on, a ref that finds no legal top slot is retried on
    the bottom side: SMD parts on opposite sides may share XY space, so only
    through-board obstacles (holes, keepouts, THT parts) and same-side parts
    block it. Seeded sides are reported in diagnostics["sides"].

    forced_bottom_refs (LLM lock_bottom hints) never scan the top side — the
    geometry is computed with the part actually on the bottom, so the forced
    side can't manufacture a same-side collision later.
    """
    region_box = bbox_from_center(region["x"], region["y"], region["w"], region["h"])
    bottom_refs = set(bottom_refs or [])
    tht_refs = set(tht_refs or [])
    forced_bottom_refs = set(forced_bottom_refs or []) - tht_refs
    existing_sides = existing_sides or {}

    # Obstacle registries by blocking scope.
    through_boxes = []      # holes/keepouts/THT parts: block both sides
    top_smd_boxes = []      # top-side SMD parts: block top placements only
    bottom_smd_boxes = []   # bottom-side SMD parts: block bottom placements only

    for ref, pose in existing_placement.items():
        box = component_bbox(
            extract_pose(pose)[0],
            extract_pose(pose)[1],
            sizes.get(ref, (3.0, 3.0)),
            rotation=extract_pose(pose)[2],
            clearance=component_clearance / 2.0,
        )
        side = existing_sides.get(ref, "top")
        if ref in tht_refs or side not in ("top", "bottom"):
            through_boxes.append(box)
        elif side == "bottom":
            bottom_smd_boxes.append(box)
        else:
            top_smd_boxes.append(box)
    for hole in holes or []:
        box = clip_bbox(hole_bbox(hole, clearance=max(component_clearance, 1.0)), region_box)
        if box is not None:
            through_boxes.append(box)
    for keepout in keepouts or []:
        ko_box = keepout_bbox(keepout)
        clipped = clip_bbox(ko_box, region_box) if ko_box is not None else None
        if clipped is not None:
            through_boxes.append(clipped)

    ordered_refs = ordered_refs or list(refs)
    placement = {ref: tuple(extract_pose(pose)) for ref, pose in existing_placement.items()}
    sides = dict(existing_sides)
    top_centers = []
    bottom_centers = []

    max_slots = max(3, int(math.ceil(math.sqrt(len(ordered_refs) + 1))) * 3)
    if allow_bottom:
        # Relief mode searches finer: the spread-friendly scoring scatters the
        # first-seeded parts across the board, and a coarse grid (~2mm) can
        # then miss every legal window for a later large part (e.g. a D5 THT
        # radial) even though most of the board area is still free.
        max_slots = max(max_slots, 16)
    xs = np.linspace(region_box[0], region_box[2], num=max_slots + 2)[1:-1]
    ys = np.linspace(region_box[1], region_box[3], num=max_slots + 2)[1:-1]
    region_cx, region_cy = region["x"], region["y"]
    candidate_points = sorted(
        ((float(x), float(y)) for y in ys for x in xs),
        key=lambda point: math.hypot(point[0] - region_cx, point[1] - region_cy),
    )

    success = True
    collision_blocks = 0
    boundary_blocks = 0
    failed_refs = []
    bottom_overflow = []

    def scan_points(blocking_boxes, local_centers, width, height, points=None):
        """Best-scoring grid slot that clears blocking_boxes (shared boundary checks)."""
        nonlocal boundary_blocks, collision_blocks
        best = None
        best_score = None
        for cx, cy in (points if points is not None else candidate_points):
            box = component_bbox(
                cx, cy, (width, height),
                rotation=0.0,
                clearance=component_clearance / 2.0,
            )
            if box[0] < region_box[0] + outline_clearance or box[2] > region_box[2] - outline_clearance:
                boundary_blocks += 1
                continue
            if box[1] < region_box[1] + outline_clearance or box[3] > region_box[3] - outline_clearance:
                boundary_blocks += 1
                continue
            if board_w is not None and board_h is not None:
                effective_outline = outline_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
                if box[0] < effective_outline or box[2] > board_w - effective_outline:
                    boundary_blocks += 1
                    continue
                if box[1] < effective_outline or box[3] > board_h - effective_outline:
                    boundary_blocks += 1
                    continue
            blocked = False
            for obstacle in blocking_boxes:
                overlap_x, overlap_y = bbox_overlap(box, obstacle)
                if overlap_x > 0 and overlap_y > 0:
                    collision_blocks += 1
                    blocked = True
                    break
            if blocked:
                continue
            center_dist = math.hypot(cx - region_cx, cy - region_cy)
            if local_centers:
                nearest_local = min(
                    math.hypot(cx - px, cy - py) for px, py in local_centers
                )
            else:
                nearest_local = 0.0
            score = center_dist - 1.25 * nearest_local
            if best_score is None or score < best_score:
                best_score = score
                best = ((cx, cy, 0.0), box)
        return best

    for ref in ordered_refs:
        width, height = coerce_size(sizes.get(ref, (3.0, 3.0)))
        is_tht = ref in tht_refs
        forced_bottom = (not is_tht) and ref in forced_bottom_refs

        # A THT part occupies both sides: it must clear everything, and once
        # placed it blocks both sides.
        blocking_top = (
            through_boxes + top_smd_boxes + bottom_smd_boxes
            if is_tht else through_boxes + top_smd_boxes
        )
        found = None
        side = "bottom" if forced_bottom else "top"
        if not forced_bottom:
            found = scan_points(blocking_top, top_centers, width, height)
        if (found is None and allow_bottom and not is_tht
                and (forced_bottom or ref in bottom_refs)):
            found = scan_points(through_boxes + bottom_smd_boxes, bottom_centers, width, height)
            side = "bottom"
        if found is None and allow_bottom:
            # Fine raster: the coarse grid can step over a narrow legal window
            # left between big parts and the boundary walls.
            fine_points = _fine_candidate_points(region_box, region_cx, region_cy)
            if not forced_bottom:
                found = scan_points(blocking_top, top_centers, width, height, points=fine_points)
                side = "top"
            if found is None and not is_tht and (forced_bottom or ref in bottom_refs):
                found = scan_points(
                    through_boxes + bottom_smd_boxes, bottom_centers, width, height,
                    points=fine_points,
                )
                side = "bottom"
        if found is None:
            success = False
            failed_refs.append(ref)
            continue
        chosen, chosen_box = found
        if is_tht:
            through_boxes.append(chosen_box)
            top_centers.append((chosen[0], chosen[1]))
            bottom_centers.append((chosen[0], chosen[1]))
        elif side == "bottom":
            bottom_smd_boxes.append(chosen_box)
            bottom_centers.append((chosen[0], chosen[1]))
            bottom_overflow.append(ref)
        else:
            top_smd_boxes.append(chosen_box)
            top_centers.append((chosen[0], chosen[1]))
        sides[ref] = side
        placement[ref] = chosen

    diagnostics = {
        "placed_count": len([ref for ref in ordered_refs if ref in placement]),
        "missing_count": len(failed_refs),
        "collision_blocks": collision_blocks,
        "boundary_blocks": boundary_blocks,
        "failed_refs": failed_refs,
        "sides": sides,
        "bottom_overflow": bottom_overflow,
    }
    return {ref: placement[ref] for ref in refs if ref in placement}, success, diagnostics


def _suggest_region_expansion(region, refs, sizes, component_clearance, diagnostics,
                              board_w, board_h, outline_clearance,
                              edge_keepout_margin=0.0):
    """Suggest a larger local region when seeding is collision-limited."""
    if board_w is None or board_h is None:
        return None

    missing_count = int(diagnostics.get("missing_count", 0))
    collision_blocks = float(diagnostics.get("collision_blocks", 0))
    boundary_blocks = float(diagnostics.get("boundary_blocks", 0))
    if missing_count <= 0 and collision_blocks <= 0:
        return None

    total_slot_area = sum(
        _component_slot_area(ref, sizes, component_clearance) for ref in refs
    )
    region_area = max(float(region["w"]) * float(region["h"]), 1e-6)
    target_fill = 0.56 if len(refs) <= 2 else 0.48 if len(refs) <= 5 else 0.40
    fill_scale = math.sqrt(total_slot_area / max(region_area * target_fill, 1e-6))
    shortage_ratio = missing_count / max(len(refs), 1)
    collision_ratio = min(1.0, collision_blocks / max(30.0, 8.0 * len(refs)))
    boundary_ratio = min(1.0, boundary_blocks / max(30.0, 8.0 * len(refs)))
    growth_scale = max(
        1.10,
        fill_scale,
        1.0 + 0.45 * shortage_ratio + 0.28 * collision_ratio + 0.18 * boundary_ratio,
    )
    if diagnostics.get("placed_count", 0) == 0:
        growth_scale = max(growth_scale, 1.35)
    if growth_scale <= 1.03:
        return None

    return _expand_region_within_board(
        region,
        min(growth_scale, 2.8),
        board_w,
        board_h,
        outline_clearance,
        edge_keepout_margin,
    )


def seed_components_in_region(
    refs,
    region,
    sizes,
    existing_placement,
    holes,
    keepouts,
    component_clearance,
    outline_clearance,
    board_w=None,
    board_h=None,
    edge_keepout_margin=0.0,
    ordered_refs=None,
    auto_expand=True,
    max_auto_expansions=2,
    allow_bottom=False,
    bottom_refs=None,
    tht_refs=None,
    existing_sides=None,
    forced_bottom_refs=None,
):
    """Deterministic fallback placement when the local RL rollout fails."""
    ordered_refs = ordered_refs or list(refs)
    best_partial = {}
    best_region = dict(region)
    best_diag = None

    tried_regions = set()
    queued_regions = [dict(region)]
    expansions_used = 0
    while queued_regions:
        current_region = queued_regions.pop(0)
        region_key = (
            round(current_region["x"], 4),
            round(current_region["y"], 4),
            round(current_region["w"], 4),
            round(current_region["h"], 4),
        )
        if region_key in tried_regions:
            continue
        tried_regions.add(region_key)

        candidate, success, diagnostics = _try_seed_components_in_region(
            refs,
            current_region,
            sizes,
            existing_placement,
            holes,
            keepouts,
            component_clearance,
            outline_clearance,
            board_w=board_w,
            board_h=board_h,
            edge_keepout_margin=edge_keepout_margin,
            ordered_refs=ordered_refs,
            allow_bottom=allow_bottom,
            bottom_refs=bottom_refs,
            tht_refs=tht_refs,
            existing_sides=existing_sides,
            forced_bottom_refs=forced_bottom_refs,
        )
        if best_diag is None or len(candidate) > len(best_partial):
            best_partial = candidate
            best_region = dict(current_region)
            best_diag = diagnostics

        if success and len(candidate) == len(refs):
            return candidate, True, dict(current_region), diagnostics

        if auto_expand and expansions_used < max_auto_expansions:
            suggested_region = _suggest_region_expansion(
                current_region,
                refs,
                sizes,
                component_clearance,
                diagnostics,
                board_w,
                board_h,
                outline_clearance,
                edge_keepout_margin=edge_keepout_margin,
            )
            if suggested_region is not None:
                queued_regions.append(suggested_region)
                expansions_used += 1

    return best_partial, False, best_region, best_diag
