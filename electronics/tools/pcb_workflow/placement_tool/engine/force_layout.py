#!/usr/bin/env python3
"""
pcb_auto_placement/force_layout.py

Force-directed placement using a spring model.
Produces initial (x, y) coordinates for each component.

Features:
  - Overlap-based repulsion using actual footprint sizes
  - Normalized attraction with ideal length
  - Cooling schedule for convergence
  - Keepout and mounting hole avoidance
  - Connector edge distribution
  - Grid-based cluster seeding
  - Distance cutoff for O(n²) performance
"""

import math

import numpy as np

from geometry_utils import bbox_center, bbox_from_center, bbox_overlap, hole_bbox, keepout_bbox, rotated_size


def force_directed_placement(G, board_w: float, board_h: float, sizes: dict,
                             clusters: dict = None, iterations: int = 300,
                             seed: int = 42, edge_clearance: float = None,
                             holes: list = None, keepouts: list = None,
                             connector_edges: dict = None,
                             frozen_components: set = None,
                             initial_positions: dict = None,
                             component_clearance: float = None,
                             rotations: dict = None,
                             edge_keepout_margin: float = 0.0) -> dict:
    """
    Compute initial placement using force-directed (spring) layout.
    """
    np.random.seed(seed)

    edge_clearance = edge_clearance or 0.0
    effective_edge_clearance = edge_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
    component_clearance = component_clearance or 0.0
    holes = holes or []
    keepouts = keepouts or []
    frozen_components = frozen_components or set()
    rotations = rotations or {}
    hole_clearance = max(component_clearance, 1.0)

    refs = list(G.nodes())
    n = len(refs)
    if n == 0:
        return {}

    ref_to_idx = {ref: i for i, ref in enumerate(refs)}
    comp_sizes = np.array([
        rotated_size(sizes.get(ref, (3.0, 3.0)), rotation=rotations.get(ref, 0.0))
        for ref in refs
    ], dtype=float)
    positions = np.zeros((n, 2), dtype=float)

    seeded_refs = set()
    if initial_positions:
        for ref, coord in initial_positions.items():
            if ref in ref_to_idx:
                positions[ref_to_idx[ref], 0] = coord[0]
                positions[ref_to_idx[ref], 1] = coord[1]
                seeded_refs.add(ref)

    if clusters and not initial_positions:
        n_clusters = len(clusters)
        cols = max(1, int(np.ceil(np.sqrt(n_clusters))))
        rows = max(1, int(np.ceil(n_clusters / cols)))
        cell_w = (board_w - 2 * effective_edge_clearance) / cols
        cell_h = (board_h - 2 * effective_edge_clearance) / rows

        cluster_centers = {}
        for idx, cluster_id in enumerate(sorted(clusters.keys())):
            col = idx % cols
            row = idx // cols
            cx = effective_edge_clearance + cell_w * (col + 0.5)
            cy = effective_edge_clearance + cell_h * (row + 0.5)
            cluster_centers[cluster_id] = (cx, cy)

        for cluster_id, cluster_refs in clusters.items():
            ccx, ccy = cluster_centers[cluster_id]
            cluster_indices = [ref_to_idx[ref] for ref in cluster_refs if ref in ref_to_idx]
            if not cluster_indices:
                continue

            local_cols = max(1, int(np.ceil(np.sqrt(len(cluster_indices)))))
            local_rows = max(1, int(np.ceil(len(cluster_indices) / local_cols)))
            slot_w = max(comp_sizes[i, 0] for i in cluster_indices) + max(component_clearance, 1.0)
            slot_h = max(comp_sizes[i, 1] for i in cluster_indices) + max(component_clearance, 1.0)
            step_x = max(slot_w, min(cell_w / max(local_cols, 1), slot_w * 1.25))
            step_y = max(slot_h, min(cell_h / max(local_rows, 1), slot_h * 1.25))
            span_x = (local_cols - 1) * step_x
            span_y = (local_rows - 1) * step_y
            start_x = np.clip(
                ccx - span_x / 2.0,
                effective_edge_clearance + slot_w / 2.0,
                board_w - effective_edge_clearance - slot_w / 2.0 - span_x
            )
            start_y = np.clip(
                ccy - span_y / 2.0,
                effective_edge_clearance + slot_h / 2.0,
                board_h - effective_edge_clearance - slot_h / 2.0 - span_y
            )

            for local_idx, ref in enumerate(cluster_refs):
                if ref not in ref_to_idx:
                    continue
                i = ref_to_idx[ref]
                row = local_idx // local_cols
                col = local_idx % local_cols
                slack_x = max(step_x - comp_sizes[i, 0], 0.0) / 3.0
                slack_y = max(step_y - comp_sizes[i, 1], 0.0) / 3.0
                positions[i, 0] = start_x + col * step_x + np.random.uniform(-slack_x, slack_x)
                positions[i, 1] = start_y + row * step_y + np.random.uniform(-slack_y, slack_y)
                seeded_refs.add(ref)

    margin = max(effective_edge_clearance, 2.0)
    for i, ref in enumerate(refs):
        if ref in seeded_refs:
            continue
        positions[i, 0] = np.random.uniform(margin, board_w - margin)
        positions[i, 1] = np.random.uniform(margin, board_h - margin)

    connector_refs = [ref for ref in refs if G.nodes[ref].get("is_connector", False)]
    if connector_refs:
        edges_list = ["right", "top", "left", "bottom"]
        for ci, ref in enumerate(connector_refs):
            i = ref_to_idx[ref]
            edge_side = connector_edges[ref].lower() if connector_edges and ref in connector_edges else edges_list[ci % len(edges_list)]

            w_i, h_i = comp_sizes[i]
            margin_x = (edge_clearance or 1.0) + w_i / 2.0
            margin_y = (edge_clearance or 1.0) + h_i / 2.0

            if edge_side == "right":
                positions[i, 0] = board_w - margin_x
                positions[i, 1] = edge_clearance + (board_h - 2 * edge_clearance) * (ci + 1) / (len(connector_refs) + 1)
            elif edge_side == "left":
                positions[i, 0] = margin_x
                positions[i, 1] = edge_clearance + (board_h - 2 * edge_clearance) * (ci + 1) / (len(connector_refs) + 1)
            elif edge_side == "top":
                positions[i, 0] = edge_clearance + (board_w - 2 * edge_clearance) * (ci + 1) / (len(connector_refs) + 1)
                positions[i, 1] = board_h - margin_y
            elif edge_side == "bottom":
                positions[i, 0] = edge_clearance + (board_w - 2 * edge_clearance) * (ci + 1) / (len(connector_refs) + 1)
                positions[i, 1] = margin_y

    edges = []
    for u, v, data in G.edges(data=True):
        if u in ref_to_idx and v in ref_to_idx:
            edges.append((ref_to_idx[u], ref_to_idx[v], data.get("weight", 1)))

    k_attract = 0.02
    k_repel = 800.0
    k_boundary = 0.5
    k_keepout = 500.0
    k_hole = 500.0
    damping = 0.9
    dt = 1.0
    ideal_length = 10.0
    repulsion_cutoff = 50.0
    initial_temp = 1.0

    velocities = np.zeros((n, 2), dtype=float)

    def component_box(index, clearance=0.0):
        return bbox_from_center(
            positions[index, 0], positions[index, 1],
            comp_sizes[index, 0], comp_sizes[index, 1],
            clearance=clearance
        )

    def push_from_box(index, obstacle_box, strength, forces):
        comp_box = component_box(index, clearance=component_clearance / 2.0)
        overlap_x, overlap_y = bbox_overlap(comp_box, obstacle_box)
        if overlap_x <= 0 or overlap_y <= 0:
            return

        obs_cx, obs_cy = bbox_center(obstacle_box)
        dx = positions[index, 0] - obs_cx
        dy = positions[index, 1] - obs_cy
        if overlap_x <= overlap_y:
            direction = 1.0 if dx >= 0 else -1.0
            forces[index, 0] += strength * overlap_x * direction
        else:
            direction = 1.0 if dy >= 0 else -1.0
            forces[index, 1] += strength * overlap_y * direction

    for step in range(iterations):
        forces = np.zeros((n, 2), dtype=float)
        temperature = initial_temp * (1.0 - step / max(iterations, 1))

        for i in range(n):
            for j in range(i + 1, n):
                dx = positions[i, 0] - positions[j, 0]
                dy = positions[i, 1] - positions[j, 1]
                dist = max(np.hypot(dx, dy), 0.1)
                if dist > repulsion_cutoff:
                    continue

                overlap_x, overlap_y = bbox_overlap(
                    component_box(i, clearance=component_clearance / 2.0),
                    component_box(j, clearance=component_clearance / 2.0)
                )

                if overlap_x > 0 and overlap_y > 0:
                    if overlap_x <= overlap_y:
                        direction = 1.0 if dx >= 0 else -1.0
                        force_mag = k_repel * overlap_x
                        forces[i, 0] += force_mag * direction
                        forces[j, 0] -= force_mag * direction
                    else:
                        direction = 1.0 if dy >= 0 else -1.0
                        force_mag = k_repel * overlap_y
                        forces[i, 1] += force_mag * direction
                        forces[j, 1] -= force_mag * direction
                else:
                    force_mag = k_repel * 0.1 / (dist ** 2)
                    fx = force_mag * dx / dist
                    fy = force_mag * dy / dist
                    forces[i, 0] += fx
                    forces[i, 1] += fy
                    forces[j, 0] -= fx
                    forces[j, 1] -= fy

        for i_idx, j_idx, weight in edges:
            dx = positions[j_idx, 0] - positions[i_idx, 0]
            dy = positions[j_idx, 1] - positions[i_idx, 1]
            dist = max(np.hypot(dx, dy), 0.1)

            force_mag = k_attract * weight * (dist - ideal_length)
            fx = force_mag * dx / dist
            fy = force_mag * dy / dist
            forces[i_idx, 0] += fx
            forces[i_idx, 1] += fy
            forces[j_idx, 0] -= fx
            forces[j_idx, 1] -= fy

        for i in range(n):
            w_half = comp_sizes[i, 0] / 2.0
            h_half = comp_sizes[i, 1] / 2.0

            if positions[i, 0] - w_half < edge_clearance:
                forces[i, 0] += k_boundary * (effective_edge_clearance - (positions[i, 0] - w_half))
            if positions[i, 0] + w_half > board_w - effective_edge_clearance:
                forces[i, 0] -= k_boundary * ((positions[i, 0] + w_half) - (board_w - effective_edge_clearance))
            if positions[i, 1] - h_half < effective_edge_clearance:
                forces[i, 1] += k_boundary * (effective_edge_clearance - (positions[i, 1] - h_half))
            if positions[i, 1] + h_half > board_h - effective_edge_clearance:
                forces[i, 1] -= k_boundary * ((positions[i, 1] + h_half) - (board_h - effective_edge_clearance))

        for ko in keepouts:
            ko_box = keepout_bbox(ko)
            if ko_box is None:
                continue
            for i in range(n):
                push_from_box(i, ko_box, k_keepout, forces)

        for hole in holes:
            hole_box = hole_bbox(hole, clearance=hole_clearance)
            for i in range(n):
                push_from_box(i, hole_box, k_hole, forces)

        for i, ref in enumerate(refs):
            if G.nodes[ref].get("is_connector", False) or ref in frozen_components:
                forces[i, 0] = 0.0
                forces[i, 1] = 0.0

        velocities = damping * velocities + forces * dt
        velocities *= temperature
        positions += velocities * dt

        for i in range(n):
            w_half = comp_sizes[i, 0] / 2.0
            h_half = comp_sizes[i, 1] / 2.0
            positions[i, 0] = np.clip(
                positions[i, 0],
                w_half + effective_edge_clearance,
                board_w - w_half - effective_edge_clearance
            )
            positions[i, 1] = np.clip(
                positions[i, 1],
                h_half + effective_edge_clearance,
                board_h - h_half - effective_edge_clearance
            )

    placement = {}
    for i, ref in enumerate(refs):
        placement[ref] = (round(float(positions[i, 0]), 2), round(float(positions[i, 1]), 2))

    return placement
