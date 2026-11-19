#!/usr/bin/env python3
"""
pcb_auto_placement/main_pipeline.py

Orchestrates the full algorithmic PCB placement pipeline:
1. Load parsed netlist, footprints, board, mechanical JSONs
2. Estimate component sizes
3. Build PCB-aware connectivity graph
4. Process connectors (r  ule-based, fixed positions)
5. Generate connector keepouts (rule-based) 
6. Call mechanical_agent for mounting holes (LLM only)
7. PCB-specific component clustering (exclude connectors)
8. Force-directed initial layout
9. GA optimization (DEAP)
10. Output placement JSON
"""

import sys
import os
import json
import csv
import math

from size_estimator import estimate_all_sizes
from graph_utils import build_netlist_graph, print_graph_summary
from clustering import cluster_components, merge_llm_blocks, print_clusters, split_dense_clusters
from cluster_analysis import (
    analyze_clusters,
    choose_center_cluster,
    order_clusters,
    summarize_cluster_table,
)
from cluster_placement import (
    order_cluster_components,
    place_clusters,
    seed_components_in_region,
)
from force_layout import force_directed_placement
from ga_optimizer import optimize
from cost_functions import compute_total_cost
from geometry_utils import bbox_from_center, component_bbox
from side_assignment import assign_sides, collect_tht_refs, infer_package_types
from rl_environment import PCBAgentEnv
from rl_agent import PPOAgent
from issue_detector import detect_issues
from placement_constraints import (
    evaluate_constraints,
    failed_hard_constraints,
    normalize_constraints,
    seed_constraints,
)
from route_feasibility import repair_route_feasibility
from resize_policy import (
    build_batch_failure_resize_target,
    build_rejection_resize_target,
    next_staged_board_resize,
)

from pcb import PCB, Component, Net
from semantic_placer import SemanticPlacer


def load_json(path: str) -> dict:
    """Load a JSON file if it exists."""
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return None


def attach_rotations(placement: dict, rotations: dict) -> dict:
    """Return placement values normalized to (x, y, rotation)."""
    result = {}
    for ref, coord in placement.items():
        if len(coord) >= 3:
            result[ref] = (coord[0], coord[1], coord[2])
        else:
            result[ref] = (coord[0], coord[1], rotations.get(ref, 0.0))
    return result


def round_up_dim(value: float, quantum: float = 0.5) -> float:
    """Round a board dimension upward to a friendly grid."""
    quantum = max(float(quantum), 1e-6)
    return math.ceil(float(value) / quantum) * quantum


def legal_inner_dims(board_w, board_h, outline_clearance, edge_keepout_margin):
    """Return usable interior size after edge exclusions."""
    effective_outline = outline_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
    inner_w = max(float(board_w) - 2.0 * effective_outline, 1.0)
    inner_h = max(float(board_h) - 2.0 * effective_outline, 1.0)
    return inner_w, inner_h


def coerce_size_entry(size_entry):
    """Normalize a size entry to (width, height)."""
    if isinstance(size_entry, tuple):
        return float(size_entry[0]), float(size_entry[1])
    return float(size_entry.get("width", 3.0)), float(size_entry.get("height", 3.0))


def required_placement_dims(sizes, cluster_info, component_clearance):
    """Return minimum usable width/height required by components and clusters."""
    max_component_w = 0.0
    max_component_h = 0.0
    for dims in sizes.values():
        if isinstance(dims, tuple):
            w, h = dims
        else:
            w = dims.get("width", 3.0)
            h = dims.get("height", 3.0)
        max_component_w = max(max_component_w, float(w) + 2.0 * component_clearance)
        max_component_h = max(max_component_h, float(h) + 2.0 * component_clearance)

    max_cluster_w = max((float(meta.get("bbox_w", 0.0)) for meta in cluster_info.values()), default=0.0)
    max_cluster_h = max((float(meta.get("bbox_h", 0.0)) for meta in cluster_info.values()), default=0.0)
    return max(max_component_w, max_cluster_w), max(max_component_h, max_cluster_h)


def expand_board_if_needed(board_w, board_h, sizes, cluster_info,
                           outline_clearance, edge_keepout_margin,
                           component_clearance):
    """
    Increase the board size if the current legal interior cannot fit the largest
    component or cluster. Width grows first, then height.
    """
    inner_w, inner_h = legal_inner_dims(board_w, board_h, outline_clearance, edge_keepout_margin)
    required_w, required_h = required_placement_dims(sizes, cluster_info, component_clearance)

    new_w = float(board_w)
    new_h = float(board_h)
    reasons = []

    if required_w > inner_w:
        delta_w = required_w - inner_w
        new_w = round_up_dim(new_w + delta_w)
        reasons.append(
            f"horizontal growth for required inner width {required_w:.2f} mm"
        )
        inner_w, inner_h = legal_inner_dims(
            new_w, new_h, outline_clearance, edge_keepout_margin
        )

    if required_h > inner_h:
        delta_h = required_h - inner_h
        new_h = round_up_dim(new_h + delta_h)
        reasons.append(
            f"vertical growth for required inner height {required_h:.2f} mm"
        )

    return new_w, new_h, reasons, required_w, required_h


def build_pending_resize(board_w, board_h, sizes, cluster_info,
                         outline_clearance, edge_keepout_margin,
                         component_clearance):
    """Return an exact resize target if the current board is still undersized."""
    resized_board_w, resized_board_h, resize_reasons, required_inner_w, required_inner_h = (
        expand_board_if_needed(
            board_w,
            board_h,
            sizes,
            cluster_info,
            outline_clearance,
            edge_keepout_margin,
            component_clearance,
        )
    )
    if resized_board_w > board_w or resized_board_h > board_h:
        return {
            "board_w": resized_board_w,
            "board_h": resized_board_h,
            "reasons": list(resize_reasons),
            "required_inner_w": required_inner_w,
            "required_inner_h": required_inner_h,
        }
    return None


def build_rejection_pending_resize(board_w, board_h, cluster_info,
                                   rejected_cluster_id, outline_clearance,
                                   edge_keepout_margin, component_clearance):
    """Return a modest staged-resize target for a repeatedly rejected cluster."""
    cluster_meta = cluster_info.get(rejected_cluster_id)
    if not cluster_meta:
        return None

    target_w, target_h, resize_reasons = build_rejection_resize_target(
        current_w=board_w,
        current_h=board_h,
        cluster_bbox_w=cluster_meta.get("bbox_w", 0.0),
        cluster_bbox_h=cluster_meta.get("bbox_h", 0.0),
        outline_clearance=outline_clearance,
        edge_keepout_margin=edge_keepout_margin,
        packing_pressure=cluster_meta.get("packing_pressure", 0.0),
        member_count=cluster_meta.get("member_count", 0),
    )
    if target_w <= board_w + 1e-6 and target_h <= board_h + 1e-6:
        return None

    required_inner_w, required_inner_h = legal_inner_dims(
        target_w, target_h, outline_clearance, edge_keepout_margin
    )
    return {
        "board_w": target_w,
        "board_h": target_h,
        "reasons": list(resize_reasons),
        "required_inner_w": required_inner_w,
        "required_inner_h": required_inner_h,
        "source": "repeated_cluster_rejection",
        "cluster_id": int(rejected_cluster_id),
    }


def build_batch_failure_pending_resize(board_w, board_h, cluster_info,
                                       missing_refs, failed_clusters,
                                       outline_clearance,
                                       edge_keepout_margin):
    """Return a modest growth target after a full failed batch."""
    missing_set = set(missing_refs or [])
    failed_cluster_ids = {cid for cid, _refs in (failed_clusters or [])}

    candidate_ids = set(failed_cluster_ids)
    for cid, meta in cluster_info.items():
        refs = set(meta.get("refs", []))
        if refs & missing_set:
            candidate_ids.add(cid)

    if not candidate_ids and missing_set:
        candidate_ids = {
            cid for cid, meta in cluster_info.items()
            if set(meta.get("refs", [])) & missing_set
        }

    if not candidate_ids and cluster_info:
        candidate_ids = set(cluster_info.keys())

    if not candidate_ids:
        return None

    def _candidate_key(cid):
        meta = cluster_info.get(cid, {})
        refs = set(meta.get("refs", []))
        overlap = len(refs & missing_set)
        return (
            overlap,
            float(meta.get("bbox_area", 0.0)),
            int(meta.get("member_count", 0)),
        )

    focus_cluster_id = max(candidate_ids, key=_candidate_key)
    focus_meta = cluster_info.get(focus_cluster_id, {})
    target_w, target_h, resize_reasons = build_batch_failure_resize_target(
        current_w=board_w,
        current_h=board_h,
        outline_clearance=outline_clearance,
        edge_keepout_margin=edge_keepout_margin,
        focus_bbox_w=focus_meta.get("bbox_w", 0.0),
        focus_bbox_h=focus_meta.get("bbox_h", 0.0),
        focus_member_count=focus_meta.get("member_count", 0),
    )
    if target_w <= board_w + 1e-6 and target_h <= board_h + 1e-6:
        return None

    required_inner_w, required_inner_h = legal_inner_dims(
        target_w, target_h, outline_clearance, edge_keepout_margin
    )
    return {
        "board_w": target_w,
        "board_h": target_h,
        "reasons": list(resize_reasons),
        "required_inner_w": required_inner_w,
        "required_inner_h": required_inner_h,
        "source": "failed_batch_fallback",
        "cluster_id": int(focus_cluster_id),
    }


def shift_edge_anchor_point(x, y, old_w, old_h, delta_w, delta_h, tolerance=5.0):
    """Keep parts that were anchored to right/bottom edges attached after resize."""
    x = float(x)
    y = float(y)
    if delta_w > 0.0 and abs(x - (old_w - 2.0)) < tolerance:
        x += delta_w
    if delta_h > 0.0 and abs(y - (old_h - 2.0)) < tolerance:
        y += delta_h
    return x, y


def shift_edge_anchor_holes(holes, old_w, old_h, delta_w, delta_h, tolerance=5.0):
    """Shift right/bottom edge anchored holes after a board resize."""
    shifted = []
    for hole in holes:
        hole_copy = dict(hole)
        x = float(hole_copy.get("x", 0.0))
        y = float(hole_copy.get("y", 0.0))
        if delta_w > 0.0 and (old_w - x) < tolerance:
            x += delta_w
        if delta_h > 0.0 and (old_h - y) < tolerance:
            y += delta_h
        hole_copy["x"] = round(x, 4)
        hole_copy["y"] = round(y, 4)
        shifted.append(hole_copy)
    return shifted


def shift_edge_anchor_keepouts(keepouts, old_w, old_h, delta_w, delta_h, tolerance=5.0):
    """Shift right/bottom edge anchored keepouts after a board resize."""
    shifted = []
    for keepout in keepouts:
        keepout_copy = dict(keepout)
        x1 = float(keepout_copy.get("x1", 0.0))
        x2 = float(keepout_copy.get("x2", 0.0))
        y1 = float(keepout_copy.get("y1", 0.0))
        y2 = float(keepout_copy.get("y2", 0.0))

        if delta_w > 0.0 and (old_w - x2) < tolerance:
            x1 += delta_w
            x2 += delta_w
        if delta_h > 0.0 and (old_h - y2) < tolerance:
            y1 += delta_h
            y2 += delta_h

        keepout_copy["x1"] = round(x1, 4)
        keepout_copy["x2"] = round(x2, 4)
        keepout_copy["y1"] = round(y1, 4)
        keepout_copy["y2"] = round(y2, 4)
        shifted.append(keepout_copy)
    return shifted


def dedupe_keepouts(keepouts):
    """Remove duplicate keepouts while preserving order."""
    seen = set()
    unique = []
    for keepout in keepouts or []:
        key = (
            round(float(keepout.get("x1", 0.0)), 4),
            round(float(keepout.get("y1", 0.0)), 4),
            round(float(keepout.get("x2", 0.0)), 4),
            round(float(keepout.get("y2", 0.0)), 4),
            keepout.get("layer", "All"),
            keepout.get("ref", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(keepout))
    return unique


def split_connector_keepouts(keepouts):
    """Separate connector/fixed keepouts from general mechanical keepouts."""
    connector_keepouts = []
    mechanical_keepouts = []
    for keepout in keepouts or []:
        ref = str(keepout.get("ref", ""))
        if ref.startswith("KEEPOUT_FIXED_"):
            connector_keepouts.append(dict(keepout))
        else:
            mechanical_keepouts.append(dict(keepout))
    return mechanical_keepouts, connector_keepouts


def infer_rotation_from_keepout(ref, keepout, sizes, component_clearance, fallback_rotation=0.0):
    """Infer whether a fixed component should be 0 or 90 deg from its keepout box."""
    width, height = coerce_size_entry(sizes.get(ref, (3.0, 3.0)))
    keepout_w = max(float(keepout.get("x2", 0.0)) - float(keepout.get("x1", 0.0)) - 2.0 * component_clearance, 0.0)
    keepout_h = max(float(keepout.get("y2", 0.0)) - float(keepout.get("y1", 0.0)) - 2.0 * component_clearance, 0.0)
    err_0 = abs(keepout_w - width) + abs(keepout_h - height)
    err_90 = abs(keepout_w - height) + abs(keepout_h - width)
    if err_90 + 1e-6 < err_0:
        return 90.0
    if err_0 + 1e-6 < err_90:
        return 0.0
    return float(fallback_rotation)


def sync_fixed_positions_from_keepouts(fixed_positions, connector_keepouts, sizes,
                                       component_clearance, pcb_components=None):
    """Snap fixed connector positions to the centers of any matching keepout boxes."""
    keepout_by_ref = {}
    for keepout in connector_keepouts or []:
        ref = str(keepout.get("ref", ""))
        if ref.startswith("KEEPOUT_FIXED_"):
            keepout_by_ref[ref.replace("KEEPOUT_FIXED_", "", 1)] = dict(keepout)

    updated = {}
    snapped_refs = []
    for ref, props in fixed_positions.items():
        keepout = keepout_by_ref.get(ref)
        if keepout is None:
            updated[ref] = dict(props)
            continue

        x1 = float(keepout.get("x1", 0.0))
        x2 = float(keepout.get("x2", 0.0))
        y1 = float(keepout.get("y1", 0.0))
        y2 = float(keepout.get("y2", 0.0))
        rotation = infer_rotation_from_keepout(
            ref,
            keepout,
            sizes,
            component_clearance,
            fallback_rotation=props.get("rotation", 0.0),
        )
        updated[ref] = {
            **dict(props),
            "x": round((x1 + x2) / 2.0, 4),
            "y": round((y1 + y2) / 2.0, 4),
            "rotation": rotation,
        }
        snapped_refs.append(ref)

        if pcb_components and ref in pcb_components:
            pcb_components[ref].ctr_x = updated[ref]["x"]
            pcb_components[ref].ctr_y = updated[ref]["y"]
            pcb_components[ref].rotation = rotation

    return updated, snapped_refs


def build_fixed_keepouts(fixed_positions, sizes, component_clearance):
    """Build keepout rectangles from fixed component positions."""
    keepouts = []
    for ref, props in fixed_positions.items():
        width, height = coerce_size_entry(sizes.get(ref, (3.0, 3.0)))
        rotation = float(props.get("rotation", 0.0))
        if int(rotation) % 180 != 0:
            width, height = height, width
        kw = width / 2.0 + component_clearance
        kh = height / 2.0 + component_clearance
        keepouts.append({
            "x1": round(float(props["x"]) - kw, 2),
            "y1": round(float(props["y"]) - kh, 2),
            "x2": round(float(props["x"]) + kw, 2),
            "y2": round(float(props["y"]) + kh, 2),
            "layer": "All",
            "ref": f"KEEPOUT_FIXED_{ref}",
        })
    return keepouts


def run_cluster_rollout(agent, G, sizes, board_w, board_h, clusters, cluster_refs,
                        region_bbox, initial_placement, holes, keepouts,
                        outline_clearance, component_clearance,
                        edge_keepout_margin=0.0):
    """Run a single local RL rollout for one cluster region."""
    env = PCBAgentEnv(
        G, sizes, board_w, board_h, clusters,
        holes=holes, keepouts=keepouts,
        edge_clearance=outline_clearance, comp_clearance=component_clearance, N=64,
        component_order=cluster_refs, region_bbox=region_bbox,
        initial_placement=initial_placement,
        edge_keepout_margin=edge_keepout_margin,
    )
    state = env.reset()
    if state is None:
        return env, False

    done = False
    rollout_success = True
    while not done:
        action, log_prob, value = agent.select_action(state, test=False)
        if action is None:
            rollout_success = False
            break
        state, reward, done, _ = env.step(action)
        if state is None and not done:
            rollout_success = False
            break

    if not all(ref in env.placement for ref in cluster_refs):
        rollout_success = False

    if rollout_success and env.placement:
        bad_refs = detect_issues(
            env.placement, sizes, board_w, board_h, holes, keepouts,
            edge_clearance=outline_clearance, comp_clearance=component_clearance,
            edge_keepout_margin=edge_keepout_margin
        )
        if any(ref in set(cluster_refs) for ref in bad_refs):
            rollout_success = False
    return env, rollout_success


def placement_keepouts(placement, sizes, clearance):
    """Convert placed component boxes into keepout rectangles."""
    keepouts = []
    for ref, pose in placement.items():
        x, y, rot = attach_rotations({ref: pose}, {})[ref]
        left, bottom, right, top = component_bbox(
            x, y, sizes.get(ref, (3.0, 3.0)), rotation=rot, clearance=clearance / 2.0
        )
        keepouts.append({
            "x1": left,
            "y1": bottom,
            "x2": right,
            "y2": top,
            "layer": "All",
            "ref": f"OCCUPIED_{ref}",
        })
    return keepouts


def expanded_region(region, scale, board_w, board_h, outline_clearance, edge_keepout_margin):
    """Expand a cluster region while keeping it inside the legal board interior."""
    effective_outline = outline_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
    max_half_w = max((board_w / 2.0) - effective_outline, region["w"] / 2.0)
    max_half_h = max((board_h / 2.0) - effective_outline, region["h"] / 2.0)
    half_w = min((region["w"] * scale) / 2.0, max_half_w)
    half_h = min((region["h"] * scale) / 2.0, max_half_h)
    cx = min(max(region["x"], effective_outline + half_w), board_w - effective_outline - half_w)
    cy = min(max(region["y"], effective_outline + half_h), board_h - effective_outline - half_h)
    return {"x": cx, "y": cy, "w": 2.0 * half_w, "h": 2.0 * half_h}


def board_inner_region(board_w, board_h, outline_clearance, edge_keepout_margin):
    """Return the full legal search window for movable components."""
    effective_outline = outline_clearance + max(float(edge_keepout_margin or 0.0), 0.0)
    width = max(board_w - 2.0 * effective_outline, 1.0)
    height = max(board_h - 2.0 * effective_outline, 1.0)
    return {
        "x": board_w / 2.0,
        "y": board_h / 2.0,
        "w": width,
        "h": height,
    }


def build_retry_regions(region, board_w, board_h, outline_clearance,
                        edge_keepout_margin, attempt_idx):
    """Return increasingly aggressive local retry regions for a failed cluster."""
    scales = [1.0, 1.2, 1.4, 1.7]
    if attempt_idx >= 2:
        scales.append(2.0)
    if attempt_idx >= 3:
        scales.append(2.4)
    if attempt_idx >= 4:
        scales.append(2.8)

    seen = set()
    regions = []
    for scale in scales:
        candidate = dict(region) if abs(scale - 1.0) < 1e-9 else expanded_region(
            region, scale, board_w, board_h, outline_clearance, edge_keepout_margin
        )
        key = (
            round(candidate["x"], 3),
            round(candidate["y"], 3),
            round(candidate["w"], 3),
            round(candidate["h"], 3),
        )
        if key not in seen:
            seen.add(key)
            regions.append(candidate)

    full_region = board_inner_region(board_w, board_h, outline_clearance, edge_keepout_margin)
    full_key = (
        round(full_region["x"], 3),
        round(full_region["y"], 3),
        round(full_region["w"], 3),
        round(full_region["h"], 3),
    )
    if full_key not in seen:
        regions.append(full_region)

    return regions


def assess_attempt(placement_xy, rotations, expected_refs, sizes, board_w, board_h,
                   holes, keepouts, outline_clearance, component_clearance,
                   edge_keepout_margin, sides=None, tht_refs=None,
                   footprint_metadata=None):
    """Return completion status for a candidate full-board placement."""
    placement_with_rot = attach_rotations(placement_xy, rotations)
    missing_refs = sorted(set(expected_refs) - set(placement_xy.keys()))
    bad_refs = []
    if placement_with_rot:
        bad_refs = sorted(detect_issues(
            placement_with_rot, sizes, board_w, board_h, holes, keepouts,
            edge_clearance=outline_clearance, comp_clearance=component_clearance,
            edge_keepout_margin=edge_keepout_margin,
            sides=sides, tht_refs=tht_refs,
            footprint_metadata=footprint_metadata,
        ))
    return placement_with_rot, missing_refs, bad_refs


def apply_board_resize(board_w, board_h, resized_board_w, resized_board_h,
                       resize_reasons, required_inner_w, required_inner_h,
                       board_config, board_json_path, placer, pcb_obj,
                       fixed_connector_positions, fixed_keepouts, holes,
                       mechanical_keepouts, connector_keepouts, sizes,
                       component_clearance, mechanical_config,
                       mechanical_json_path):
    """Apply a deferred board resize and keep anchored geometry aligned."""
    old_board_w = board_w
    old_board_h = board_h
    delta_w = resized_board_w - old_board_w
    delta_h = resized_board_h - old_board_h
    board_w = resized_board_w
    board_h = resized_board_h

    print("\n[Step 4.6] Auto-expanding board for placement capacity...")
    print(
        f"  Board resized: {old_board_w:.2f} x {old_board_h:.2f} -> "
        f"{board_w:.2f} x {board_h:.2f} mm"
    )
    print(
        f"  Required legal interior: {required_inner_w:.2f} x {required_inner_h:.2f} mm"
    )
    for reason in resize_reasons:
        print(f"  Reason: {reason}")

    board_config["width"] = board_w
    board_config["height"] = board_h
    board_config["final_width"] = board_w
    board_config["final_height"] = board_h
    board_config["final_aspect_ratio"] = round(board_w / max(board_h, 1e-6), 4)
    board_config["auto_resized"] = True
    board_config["resize_reasons"] = list(resize_reasons)
    board_config["size_source"] = "llm_initial_plus_geometric_safeguard"
    with open(board_json_path, "w") as f:
        json.dump(board_config, f, indent=2)

    placer.board_outline["width"] = board_w
    placer.board_outline["height"] = board_h
    pcb_obj.x_range = [0.0, float(board_w)]
    pcb_obj.y_range = [0.0, float(board_h)]

    shifted_fixed_positions = {}
    for ref, props in fixed_connector_positions.items():
        new_x, new_y = shift_edge_anchor_point(
            props["x"], props["y"], old_board_w, old_board_h, delta_w, delta_h
        )
        shifted_props = dict(props)
        shifted_props["x"] = round(new_x, 4)
        shifted_props["y"] = round(new_y, 4)
        shifted_fixed_positions[ref] = shifted_props
        if ref in pcb_obj.components:
            pcb_obj.components[ref].ctr_x = shifted_props["x"]
            pcb_obj.components[ref].ctr_y = shifted_props["y"]
    fixed_connector_positions = shifted_fixed_positions

    fixed_keepouts = shift_edge_anchor_keepouts(
        fixed_keepouts, old_board_w, old_board_h, delta_w, delta_h
    )
    holes = shift_edge_anchor_holes(
        holes, old_board_w, old_board_h, delta_w, delta_h
    )
    mechanical_keepouts = shift_edge_anchor_keepouts(
        mechanical_keepouts, old_board_w, old_board_h, delta_w, delta_h
    )
    connector_keepouts = shift_edge_anchor_keepouts(
        connector_keepouts, old_board_w, old_board_h, delta_w, delta_h
    )
    fixed_connector_positions, snapped_refs = sync_fixed_positions_from_keepouts(
        fixed_connector_positions,
        connector_keepouts,
        sizes,
        component_clearance,
        pcb_components=pcb_obj.components,
    )
    if snapped_refs:
        print(
            f"  Re-snapped fixed connectors after board resize: {sorted(snapped_refs)}"
        )
    fixed_keepouts = build_fixed_keepouts(
        fixed_connector_positions, sizes, component_clearance
    )
    connector_keepouts = dedupe_keepouts(fixed_keepouts)
    keepouts = mechanical_keepouts + connector_keepouts

    if mechanical_config is not None:
        mechanical_config["holes"] = holes
        mechanical_config["keepouts"] = mechanical_keepouts
        mechanical_config["connector_keepouts"] = connector_keepouts
        mechanical_config["mechanical_keepouts"] = mechanical_keepouts
        with open(mechanical_json_path, "w") as f:
            json.dump(mechanical_config, f, indent=2)

    return (
        board_w,
        board_h,
        fixed_connector_positions,
        fixed_keepouts,
        holes,
        mechanical_keepouts,
        connector_keepouts,
        keepouts,
        mechanical_config,
    )


def run_pipeline(input_dir: str, prefix: str,
                 ga_generations: int = 100, ga_population: int = 50,
                 allow_bottom: bool = False, bottom_bias: float = 20.0,
                 side_hints: dict = None, cluster_hints: dict = None,
                 relative_constraints: list = None):
    """
    Run the full algorithmic placement pipeline.

    Args:
        input_dir: Directory containing CSVs and JSONs
        prefix: Project prefix (e.g. "myboard")
        ga_generations: number of GA generations
        ga_population: GA population size
        allow_bottom: permit SMD parts on the bottom side (post-GA side pass)
        bottom_bias: per-bottom-part cost bias; top stays preferred unless a
            flip buys real packing/wire gain
        side_hints: optional LLM placement hints, requires allow_bottom=true.
            lock_top: hard — refs never leave the top side (not through-
                blocking; unlike THT they only occupy the top face)
            lock_bottom: hard — refs are forced onto the bottom at seeding
                time (SMD only; THT/connector refs raise ValueError)
            allow_bottom: whitelist — only these refs may overflow or flip
                to the bottom
            prefer_top: soft — bias multiplier (prefer_top_weight, default
                5.0) applied to those refs' bottom cost
            Unknown refs are dropped with a warning.
        cluster_hints: optional LLM functional-block hints (side-independent,
            works with or without allow_bottom).
            blocks: [{"name": "power", "refs": [...]}] — keep_together: every
                cluster holding one of the block's refs merges into a single
                cluster (re-applied after the dense-split pass so the block
                never re-splits). Unknown refs warn+drop.
            separate: [["power", "analog"], ...] — block-name pairs that get
                a centroid-repulsion cost term in the GA
                (separation_distance, default 5.0 mm). Pairs naming an
                undefined block warn+drop.
    """
    print("=" * 60)
    print("  PCB Auto-Placement Pipeline")
    print("=" * 60)

    # --- Step 0: Load input data ---
    print(f"\n[Step 0] Loading input data from {input_dir}...")

    # Load components from CSV
    components_csv = os.path.join(input_dir, "components.csv")
    if os.path.exists(components_csv):
        print("  --> Loading components from CSV...")
        with open(components_csv, 'r') as f:
            components_list = list(csv.DictReader(f))
    else:
        print(f"  --> ERROR: components.csv not found in {input_dir}")
        sys.exit(1)

    # Load configurations
    footprints_json_path = os.path.join(input_dir, f"{prefix}_footprints.json")
    board_json_path = os.path.join(input_dir, f"{prefix}_board.json")
    drc_json_path = os.path.join(input_dir, f"{prefix}_drc.json")
    layer_json_path = os.path.join(input_dir, f"{prefix}_layers.json")
    mechanical_json_path = os.path.join(input_dir, f"{prefix}_mechanical.json")

    with open(footprints_json_path, 'r') as f:
        footprint_mapping = json.load(f)

    relative_constraints = normalize_constraints(
        relative_constraints,
        {ref for ref in footprint_mapping if not str(ref).startswith("#")},
        footprint_mapping,
        allow_bottom=allow_bottom,
    )
    if relative_constraints:
        print(f"  Geometry-aware placement constraints: {len(relative_constraints)}")

    board_config = load_json(board_json_path)
    drc_config = load_json(drc_json_path)
    layer_config = load_json(layer_json_path)

    if not board_config:
        raise FileNotFoundError(f"Missing required board config: {prefix}_board.json")
    if not drc_config:
        raise FileNotFoundError(f"Missing required DRC config: {prefix}_drc.json")

    board_config = dict(board_config)
    board_w = float(board_config["width"])
    board_h = float(board_config["height"])
    initial_aspect_ratio = round(board_w / max(board_h, 1e-6), 4)
    board_config.setdefault("aspect_ratio", initial_aspect_ratio)
    board_config.setdefault("size_source", "llm_initial_suggestion")
    board_config.setdefault("auto_resize_allowed", True)
    board_config.setdefault("max_resize_rounds", None)
    board_config.setdefault("initial_width", board_w)
    board_config.setdefault("initial_height", board_h)
    board_config.setdefault("final_width", board_w)
    board_config.setdefault("final_height", board_h)
    board_config.setdefault("final_aspect_ratio", initial_aspect_ratio)
    board_config.setdefault("auto_resized", False)
    outline_clearance = drc_config["outline_clearance"]
    component_clearance = drc_config["clearance"]
    # Movable parts may use the board edge area as long as they remain inside the
    # real outline and avoid fixed-component/hole keepouts.
    movable_edge_keepout = 0.0

    print(f"  Board: {board_w} x {board_h} mm")
    print(f"  Board size source: {board_config.get('size_source')}")
    print(f"  Components: {len(components_list)}")

    # Load nets from CSV if available
    nets_csv = os.path.join(input_dir, "nets.csv")
    if os.path.exists(nets_csv):
        with open(nets_csv, 'r') as f:
            nets_list = list(csv.DictReader(f))
        print(f"  Nets: {len(nets_list)}")

    print(f"  DRC outline clearance: {outline_clearance} mm")
    print(f"  DRC component clearance: {component_clearance} mm")
    print(f"  Additional movable edge keepout band: {movable_edge_keepout} mm")

    mechanical_config = load_json(mechanical_json_path)

    # --- Step 1: Estimate component sizes ---
    print("\n[Step 1] Estimating component sizes...")
    sizes = estimate_all_sizes(footprint_mapping)

    # --- Step 2: Build PCB-aware connectivity graph ---
    print("\n[Step 2] Building PCB-aware connectivity graph (power nets filtered)...")
    G = build_netlist_graph(input_dir, footprint_mapping)
    print_graph_summary(G)

    # --- Step 2.5: Semantic Placement (Connectors & Anchoring) ---
    print("\n[Step 2.5] Semantic Placement - Classification & Initial Layout...")

    # Initialize PCB object from existing data
    pcb_obj = PCB(width=board_w, height=board_h)
    pcb_obj.outline_clearance = float(outline_clearance)
    pcb_obj.component_clearance = float(component_clearance)
    pcb_obj.connector_edge_gap = max(float(outline_clearance), 1.0)

    # Populate components
    for ref, node_data in G.nodes(data=True):
        size = sizes.get(ref, (10.0, 10.0))
        if isinstance(size, tuple):
            w, h = size
        else:
            w = size.get("width", 10.0)
            h = size.get("height", 10.0)

        fp_raw = node_data.get('footprint', '')
        if isinstance(fp_raw, dict):
            fp_name = fp_raw.get("class", "Unknown")
        else:
            fp_name = fp_raw

        comp = Component(
            name=ref,
            footprint_name=fp_name,
            value=node_data.get('value', ''),
            width=w,
            height=h
        )
        pcb_obj.add_component(comp)

    # Populate nets (from Graph edges to get signal connectivity)
    for u, v, data in G.edges(data=True):
        for net_name in data.get('nets', []):
            if net_name not in pcb_obj.nets:
                pcb_obj.nets[net_name] = Net(net_name)
            # Add pins (simplified: just ref)
            if not any(p[0] == u for p in pcb_obj.nets[net_name].pins):
                pcb_obj.nets[net_name].pins.append((u, "1"))
            if not any(p[0] == v for p in pcb_obj.nets[net_name].pins):
                pcb_obj.nets[net_name].pins.append((v, "1"))

    # If a mechanical file already exists, let semantic placement avoid those obstacles too.
    if mechanical_config:
        for hole in mechanical_config.get("holes", []):
            radius = hole.get("radius")
            if radius is None:
                radius = float(hole.get("diameter", 0.0)) / 2.0
            pcb_obj.add_hole(float(hole.get("x", 0.0)), float(hole.get("y", 0.0)), radius=radius)
        for keepout in mechanical_config.get("keepouts", []):
            pcb_obj.keepouts.append(dict(keepout))

    # Run Semantic Placer
    placer = SemanticPlacer(pcb_obj)
    placer.run()

    # An explicit hard relationship may override a rule-based semantic anchor
    # (for example, a protection diode classified FIXED but explicitly requested
    # to move clear of a connector). Real connector/power-interface anchors stay
    # fixed unless the rule deliberately opts in with allow_move_fixed=true.
    constraint_move_requests = {}
    for item in relative_constraints:
        if not item.get("hard", True):
            continue
        if item["type"] in {
            "minimum_clearance", "maximum_distance", "pad_proximity",
            "keep_together", "keep_apart", "align", "relative_position",
        }:
            ref = str(item.get("move_ref") or item["a"]["ref"])
        elif item["type"] in {"fixed_position", "orientation"}:
            ref = item["target"]["ref"]
        else:
            continue
        constraint_move_requests[ref] = bool(item.get("allow_move_fixed", False))

    for ref, allow_fixed_override in constraint_move_requests.items():
        comp = pcb_obj.components.get(ref)
        if comp is None or not comp.fixed:
            continue
        semantic_name = getattr(getattr(comp, "semantic_class", None), "name", "")
        interface_name = getattr(getattr(comp, "interface_type", None), "name", "")
        mechanically_anchored = semantic_name == "CONNECTOR" or interface_name == "POWER"
        if mechanically_anchored and not allow_fixed_override:
            print(
                f"  [Placement constraints] {ref} remains a fixed connector/power "
                "interface; set allow_move_fixed=true only if moving it is mechanically valid"
            )
            continue
        comp.fixed = False
        print(f"  [Placement constraints] released semantic anchor for movable ref {ref}")

    # Extract results: Fixed positions and Edge assignments
    fixed_connector_positions = {}
    connector_refs = []
    connector_edges = {} # Legacy compatibility
    
    # Generate keepouts for ALL fixed components so they act as solid obstacles
    fixed_keepouts = []

    for ref, comp in pcb_obj.components.items():
        if comp.fixed:
            fixed_connector_positions[ref] = {"x": comp.ctr_x, "y": comp.ctr_y, "rotation": comp.rotation}
            connector_refs.append(ref) # Remove ALL fixed components from active optimization
    fixed_keepouts = build_fixed_keepouts(
        fixed_connector_positions, sizes, component_clearance
    )

    # Update board size if changed by SemanticPlacer
    board_w = placer.board_outline["width"]
    board_h = placer.board_outline["height"]
    print(f"  Final Board Size: {board_w:.2f} x {board_h:.2f}")

    # Reconstruct connector_edges and connector_keepouts for downstream agents
    connector_keepouts = []
    edge_keepout_width = 10.0
    edge_map = {"NORTH": "top", "SOUTH": "bottom", "WEST": "left", "EAST": "right"}

    for edge_name, role in placer.board_outline["edges"].items():
        legacy_edge = edge_map.get(edge_name, "bottom")
        if role.name != 'NONE':
            # Identify which connectors are on this edge for connector_edges dict
            # Stage 7 places them at W/2 or H/2. We'll find them by position.
            for ref, props in fixed_connector_positions.items():
                # Simplified check: if it's near the edge
                if edge_name == "NORTH" and abs(props["y"] - 2.0) < 5.0: connector_edges[ref] = legacy_edge
                elif edge_name == "SOUTH" and abs(props["y"] - (board_h - 2.0)) < 5.0: connector_edges[ref] = legacy_edge
                elif edge_name == "WEST" and abs(props["x"] - 2.0) < 5.0: connector_edges[ref] = legacy_edge
                elif edge_name == "EAST" and abs(props["x"] - (board_w - 2.0)) < 5.0: connector_edges[ref] = legacy_edge

            pass

    print(f"  Generated {len(connector_keepouts)} connector keepout zones")

    # --- Step 2.7: Mechanical config (mounting holes) ---
    # The LLM mechanical agent has been removed; Agent-EDA supplies <prefix>_mechanical.json
    # via the pcb-layout skill. If it is missing, fall back to safe empty defaults so the
    # pipeline still produces a board (no mounting holes / keepouts).
    print("\n[Step 2.7] Loading mechanical config (mounting holes)...")
    if not mechanical_config:
        print("  [WARN] mechanical config not found and the LLM mechanical agent has been "
              "removed. Using empty defaults (no mounting holes). Provide "
              "<prefix>_mechanical.json via the pcb-layout skill to add holes/keepouts.")
        mechanical_config = {
            "holes": [],
            "connector_edges": {},
            "connector_keepouts": [],
            "keepouts": [],
            "mechanical_keepouts": [],
        }
        with open(mechanical_json_path, 'w') as f:
            json.dump(mechanical_config, f, indent=2)
        print(f"  Saved default mechanical config to: {mechanical_json_path}")

    # Extract holes and mechanical keepouts
    holes = mechanical_config.get("holes", [])
    raw_keepouts = mechanical_config.get("keepouts", [])
    listed_connector_keepouts = mechanical_config.get("connector_keepouts", [])
    raw_mechanical_keepouts = mechanical_config.get("mechanical_keepouts", [])
    split_mechanical_keepouts, split_connector_boxes = split_connector_keepouts(raw_keepouts)
    mechanical_keepouts = dedupe_keepouts(raw_mechanical_keepouts + split_mechanical_keepouts)
    connector_keepouts = dedupe_keepouts(listed_connector_keepouts + split_connector_boxes)

    if connector_keepouts:
        fixed_connector_positions, snapped_refs = sync_fixed_positions_from_keepouts(
            fixed_connector_positions,
            connector_keepouts,
            sizes,
            component_clearance,
            pcb_components=pcb_obj.components,
        )
        if snapped_refs:
            print(
                f"  Snapped {len(snapped_refs)} fixed connectors to mechanical keepout centers: "
                f"{sorted(snapped_refs)}"
            )

    fixed_keepouts = build_fixed_keepouts(
        fixed_connector_positions, sizes, component_clearance
    )
    connector_keepouts = dedupe_keepouts(fixed_keepouts)

    # Combine all keepouts
    keepouts = mechanical_keepouts + connector_keepouts

    mechanical_config["holes"] = holes
    mechanical_config["keepouts"] = mechanical_keepouts
    mechanical_config["connector_keepouts"] = connector_keepouts
    mechanical_config["mechanical_keepouts"] = mechanical_keepouts
    with open(mechanical_json_path, "w") as f:
        json.dump(mechanical_config, f, indent=2)

    print(f"  Mounting holes: {len(holes)}")
    print(f"  Keepout zones: {len(keepouts)}")

    # --- Step 3: Remove connectors from graph for GA optimization ---
    print("\n[Step 3] Removing connectors from optimization graph...")
    G_optimization = G.copy()
    for ref in connector_refs:
        if ref in G_optimization:
            G_optimization.remove_node(ref)
    print(f"  Optimization graph: {len(G.nodes())} nodes -> {len(G_optimization.nodes())} nodes (connectors excluded)")

    # --- Step 4: PCB-specific component clustering (NON-CONNECTOR components only) ---
    print("\n[Step 4] PCB-specific component clustering...")
    base_clusters = cluster_components(G_optimization)

    # LLM keep_together blocks: validated against the optimization graph
    # (connectors were removed above, so connector refs in a block warn+drop),
    # then folded into the base map so both dense-split passes inherit them.
    cluster_hint_blocks = []
    separation_pairs = []
    separation_distance = 5.0
    blocks_by_name = {}
    if cluster_hints:
        known_refs = set(G_optimization.nodes())
        for block in cluster_hints.get("blocks") or []:
            name = str(block.get("name") or "").strip()
            raw_refs = {str(ref) for ref in block.get("refs") or []}
            dropped = sorted(raw_refs - known_refs)
            valid_refs = sorted(raw_refs & known_refs)
            if dropped:
                print(
                    f"  [Cluster hints] block '{name}': dropping unknown/connector "
                    f"refs {dropped}"
                )
            if len(valid_refs) < 2:
                print(
                    f"  [Cluster hints] block '{name}' skipped "
                    f"(<2 valid refs: {valid_refs})"
                )
                continue
            cluster_hint_blocks.append({"name": name, "refs": valid_refs})
        separation_distance = float(cluster_hints.get("separation_distance", 5.0))
        blocks_by_name = {b["name"]: set(b["refs"]) for b in cluster_hint_blocks}
        for pair in cluster_hints.get("separate") or []:
            try:
                name_a, name_b = str(pair[0]), str(pair[1])
            except (TypeError, IndexError):
                print(f"  [Cluster hints] malformed separate pair {pair!r}; dropping")
                continue
            if name_a not in blocks_by_name or name_b not in blocks_by_name:
                print(
                    f"  [Cluster hints] separate pair [{name_a}, {name_b}] names an "
                    f"undefined block; dropping"
                )
                continue
            separation_pairs.append((name_a, name_b))
        if cluster_hint_blocks or separation_pairs:
            print(
                f"  [Cluster hints] blocks={[(b['name'], b['refs']) for b in cluster_hint_blocks]} "
                f"separate={separation_pairs} separation_distance={separation_distance}mm"
            )

    if cluster_hint_blocks:
        base_clusters = merge_llm_blocks(base_clusters, cluster_hint_blocks)

    initial_cluster_count = len(base_clusters)
    clusters = split_dense_clusters(
        G_optimization,
        base_clusters,
        sizes,
        board_w,
        board_h,
        outline_clearance,
        movable_edge_keepout,
        component_clearance,
    )
    if cluster_hint_blocks:
        # Re-fold after the split: a keep_together block must never re-split.
        clusters = merge_llm_blocks(clusters, cluster_hint_blocks)
    if len(clusters) != initial_cluster_count:
        print(
            f"  Dense-cluster split pass: {initial_cluster_count} -> "
            f"{len(clusters)} clusters for current board interior"
        )
    print(f"  Found {len(clusters)} clusters:")
    print_clusters(clusters, G)

    print("\n[Step 4.5] Analyzing cluster roles and global regions...")
    cluster_info = analyze_clusters(
        G, G_optimization, clusters, sizes, pcb_obj.components,
        fixed_connector_positions, board_w, board_h, holes, keepouts,
        component_clearance, outline_clearance
    )

    pending_resize = build_pending_resize(
        board_w,
        board_h,
        sizes,
        cluster_info,
        outline_clearance,
        movable_edge_keepout,
        component_clearance,
    )
    board_config["final_width"] = board_w
    board_config["final_height"] = board_h
    board_config["final_aspect_ratio"] = round(board_w / max(board_h, 1e-6), 4)
    if board_config.get("auto_resize_allowed", True) and pending_resize is not None:
        print(
            "\n[Step 4.6] Board growth is deferred until a full batch of "
            "placement attempts fails on the raw LLM board size..."
        )
        print(
            f"  LLM board size for batch 1: {board_w:.2f} x {board_h:.2f} mm"
        )
        print(
            f"  Ultimate fallback target if later batches still fail: "
            f"{pending_resize['board_w']:.2f} x {pending_resize['board_h']:.2f} mm"
        )
        print(
            f"  Required legal interior: {pending_resize['required_inner_w']:.2f} x "
            f"{pending_resize['required_inner_h']:.2f} mm"
        )
        for reason in pending_resize["reasons"]:
            print(f"  Deferred resize reason: {reason}")

    board_config["auto_resized"] = False
    board_config["resize_reasons"] = []
    with open(board_json_path, "w") as f:
        json.dump(board_config, f, indent=2)

    center_cluster_id = choose_center_cluster(cluster_info)
    cluster_order = order_clusters(cluster_info, center_cluster_id)
    if cluster_order:
        print(f"  Center cluster: C{center_cluster_id}")
        for row in summarize_cluster_table(cluster_info, cluster_order, center_cluster_id):
            print(row)
    else:
        print("  No movable clusters detected.")

    attempts_per_board_size = 5
    max_resize_rounds = board_config.get("max_resize_rounds")
    if max_resize_rounds is not None:
        max_resize_rounds = int(max_resize_rounds)
    best_rl_placement = {}
    best_rl_cost = float("inf")
    component_rotations = {}
    optimized_placement = {}
    final_cost = float("inf")
    cluster_regions = {}

    # --- Step 5: Hierarchical cluster-first RL placement ---
    print("\n[Step 5] Processing cluster-first RL placement...")

    if len(G_optimization.nodes()) == 0:
        print("  ⚠️ No non-connector components to optimize. Skipping RL placement.")
        best_rl_cost = 0.0
        final_cost = 0.0
    else:
        expected_movable_refs = sorted(G_optimization.nodes())
        last_missing_refs = []
        last_bad_refs = []
        last_failed_clusters = []
        resize_round_idx = 0
        total_attempt_idx = 0
        placement_succeeded = False

        # Bottom-side relief policy (opt-in): THT parts and connectors stay on
        # top; eligible SMD refs may overflow to the bottom during cluster
        # seeding when the top side runs out of slots.
        if side_hints and not allow_bottom:
            raise ValueError(
                "placement.side_hints requires allow_bottom=true: hints control "
                "bottom-side policy, and without it every part stays on top."
            )
        tht_locked_refs = set()
        bottom_eligible_refs = set()
        # LLM side hints (locked sets are enforced at seeding and Step 7.5).
        locked_top_refs = set()
        locked_bottom_refs = set()
        prefer_top_weights = {}
        best_attempt_sides = {}
        constraint_required_top = {
            item["target"]["ref"] for item in relative_constraints
            if item["type"] == "required_side" and item["side"] == "top"
        }
        constraint_required_bottom = {
            item["target"]["ref"] for item in relative_constraints
            if item["type"] == "required_side" and item["side"] == "bottom"
        }
        hard_same_side_pairs = [
            {item["a"]["ref"], item["b"]["ref"]}
            for item in relative_constraints
            if item.get("hard") and item.get("same_side")
        ]
        same_side_groups = []
        for pair in hard_same_side_pairs:
            touching = [group for group in same_side_groups if group & pair]
            merged = set(pair)
            for group in touching:
                merged |= group
                same_side_groups.remove(group)
            same_side_groups.append(merged)
        for group in same_side_groups:
            if group & constraint_required_bottom:
                constraint_required_bottom |= group
            else:
                # With no explicit bottom requirement, top is the conservative
                # deterministic way to guarantee same-side placement.
                constraint_required_top |= group
        conflicting_required_sides = sorted(
            constraint_required_top & constraint_required_bottom
        )
        if conflicting_required_sides:
            raise ValueError(
                "Placement constraints require the same refs on both top and bottom: "
                f"{conflicting_required_sides}"
            )
        if allow_bottom:
            tht_locked_refs = collect_tht_refs(
                G_optimization, footprint_mapping,
                fixed_refs=fixed_connector_positions.keys(),
            )
            bottom_eligible_refs = set(G_optimization.nodes()) - tht_locked_refs
            print(
                f"  [Bottom relief] {len(bottom_eligible_refs)} SMD ref(s) may overflow "
                f"to bottom; {len(tht_locked_refs)} THT/connector ref(s) locked to top"
            )

            if side_hints:
                known_refs = set(G_optimization.nodes()) | set(fixed_connector_positions.keys())
                raw_lock_top = {str(r) for r in side_hints.get("lock_top") or []}
                raw_lock_bottom = {str(r) for r in side_hints.get("lock_bottom") or []}
                raw_prefer_top = {str(r) for r in side_hints.get("prefer_top") or []}
                raw_whitelist = {str(r) for r in side_hints.get("allow_bottom") or []}
                unknown = sorted(
                    (raw_lock_top | raw_lock_bottom | raw_prefer_top | raw_whitelist)
                    - known_refs
                )
                if unknown:
                    print(f"  [Side hints] WARNING dropping unknown refs: {unknown}")

                # lock_bottom is physically impossible for through-board refs.
                invalid_bottom = sorted(
                    (raw_lock_bottom & known_refs) & tht_locked_refs
                )
                if invalid_bottom:
                    raise ValueError(
                        f"side_hints.lock_bottom names THT/connector/fixed refs whose "
                        f"pins pierce the board and cannot sit on the bottom: "
                        f"{invalid_bottom}"
                    )

                redundant_top = sorted(raw_lock_top & tht_locked_refs)
                if redundant_top:
                    print(
                        f"  [Side hints] lock_top redundant for already-locked "
                        f"THT/connector refs: {redundant_top}"
                    )
                locked_top_refs = (raw_lock_top & known_refs) - tht_locked_refs
                locked_bottom_refs = (raw_lock_bottom & known_refs) - tht_locked_refs
                if raw_whitelist:
                    whitelisted = (
                        (raw_whitelist & known_refs)
                        - tht_locked_refs - locked_top_refs - locked_bottom_refs
                    )
                    dropped = sorted(
                        (raw_whitelist & known_refs) - whitelisted - set(unknown)
                    )
                    if dropped:
                        print(
                            f"  [Side hints] whitelist refs ignored (locked or THT): "
                            f"{dropped}"
                        )
                    bottom_eligible_refs &= whitelisted
                else:
                    bottom_eligible_refs -= locked_top_refs | locked_bottom_refs

                prefer_top_multiplier = float(side_hints.get("prefer_top_weight", 5.0))
                prefer_top_weights = {
                    ref: prefer_top_multiplier
                    for ref in (raw_prefer_top & known_refs) - tht_locked_refs
                }
                print(
                    f"  [Side hints] lock_top={sorted(locked_top_refs)} "
                    f"lock_bottom={sorted(locked_bottom_refs)} "
                    f"prefer_top={sorted(prefer_top_weights)} "
                    f"whitelist={len(bottom_eligible_refs)} eligible"
                )

            invalid_constraint_bottom = sorted(
                constraint_required_bottom &
                (tht_locked_refs | set(fixed_connector_positions.keys()))
            )
            if invalid_constraint_bottom:
                raise ValueError(
                    "Hard required_side=bottom constraints name through-hole or fixed "
                    f"connector refs that must remain on top: {invalid_constraint_bottom}"
                )
            locked_top_refs |= constraint_required_top - tht_locked_refs
            locked_bottom_refs |= constraint_required_bottom
            bottom_eligible_refs -= locked_top_refs | locked_bottom_refs

        while True:
            batch_rejected_counts = {}
            batch_rejected_refs = {}
            for board_attempt_idx in range(1, attempts_per_board_size + 1):
                total_attempt_idx += 1
                print(
                    f"\n[Placement Attempt {board_attempt_idx}/{attempts_per_board_size}] "
                    f"Board stage {resize_round_idx + 1} @ {board_w:.1f} x {board_h:.1f} mm"
                )

                attempt_grid_n = min(48 + 8 * (total_attempt_idx - 1), 80)
                cluster_regions = place_clusters(
                    cluster_order, cluster_info, board_w, board_h,
                    holes, keepouts, fixed_keepouts, outline_clearance,
                    fixed_connector_positions, component_clearance,
                    edge_keepout_margin=movable_edge_keepout, grid_n=attempt_grid_n
                )
                for cid in cluster_order:
                    region = cluster_regions[cid]
                    print(
                        f"    Region C{cid}: center=({region['x']:.1f}, {region['y']:.1f}), "
                        f"size={region['w']:.1f}x{region['h']:.1f}, role={region['role']}, "
                        f"scale={region.get('scale', 1.0):.2f}"
                    )

                agent = PPOAgent(N=64)
                accumulated_placement = {}
                accumulated_sides = {}
                failed_clusters = []

                for cid in cluster_order:
                    cluster_refs = order_cluster_components(
                        cluster_regions[cid]["refs"], G_optimization, sizes, pcb_obj.components
                    )
                    region_bbox = bbox_from_center(
                        cluster_regions[cid]["x"],
                        cluster_regions[cid]["y"],
                        cluster_regions[cid]["w"],
                        cluster_regions[cid]["h"],
                    )
                    best_cluster_placement = None
                    best_cluster_cost = float("inf")

                    for rollout_idx in range(3):
                        env, rollout_success = run_cluster_rollout(
                            agent, G_optimization, sizes, board_w, board_h, clusters,
                            cluster_refs, region_bbox, accumulated_placement, holes, keepouts,
                            outline_clearance, component_clearance,
                            edge_keepout_margin=movable_edge_keepout
                        )

                        if rollout_success and env.placement:
                            current_cost = compute_total_cost(
                                env.placement, G_optimization, sizes, board_w, board_h, holes, keepouts,
                                outline_clearance=outline_clearance, component_clearance=component_clearance,
                                edge_keepout_margin=movable_edge_keepout
                            )
                            if current_cost < best_cluster_cost:
                                best_cluster_cost = current_cost
                                best_cluster_placement = dict(env.placement)

                    if best_cluster_placement is not None:
                        accumulated_placement = best_cluster_placement
                        print(
                            f"  Cluster C{cid}: placed {len(cluster_refs)} parts in RL region "
                            f"with cost {best_cluster_cost:.1f}"
                        )
                        continue

                    retry_regions = build_retry_regions(
                        cluster_regions[cid], board_w, board_h, outline_clearance,
                        movable_edge_keepout, total_attempt_idx
                    )

                    fallback_local = {}
                    fallback_success = False
                    fallback_diag = {}
                    best_partial = {}
                    best_partial_region = dict(cluster_regions[cid])
                    chosen_retry_region = dict(cluster_regions[cid])
                    for retry_region in retry_regions:
                        candidate_local, candidate_success, candidate_region, candidate_diag = seed_components_in_region(
                            cluster_refs,
                            retry_region,
                            sizes,
                            accumulated_placement,
                            holes,
                            keepouts,
                            component_clearance,
                            outline_clearance,
                            board_w=board_w,
                            board_h=board_h,
                            edge_keepout_margin=movable_edge_keepout,
                            ordered_refs=cluster_refs,
                            allow_bottom=allow_bottom,
                            bottom_refs=bottom_eligible_refs,
                            tht_refs=tht_locked_refs,
                            existing_sides=accumulated_sides,
                            forced_bottom_refs=locked_bottom_refs,
                        )
                        if len(candidate_local) > len(best_partial):
                            best_partial = candidate_local
                            best_partial_region = dict(candidate_region)
                        if candidate_success and len(candidate_local) == len(cluster_refs):
                            fallback_local = candidate_local
                            fallback_success = True
                            fallback_diag = candidate_diag
                            chosen_retry_region = dict(candidate_region)
                            break
                        if candidate_diag.get("failed_refs"):
                            rr = retry_region
                            print(
                                f"    Seeding retry missed {candidate_diag['failed_refs']} "
                                f"(collisions={candidate_diag.get('collision_blocks')}, "
                                f"boundary={candidate_diag.get('boundary_blocks')}, "
                                f"region={rr['w']:.1f}x{rr['h']:.1f}@({rr['x']:.1f},{rr['y']:.1f}))"
                            )

                    if not fallback_local:
                        fallback_local = best_partial
                        chosen_retry_region = best_partial_region
                    if fallback_success and len(fallback_local) == len(cluster_refs):
                        cluster_regions[cid].update(chosen_retry_region)
                        for ref, pose in fallback_local.items():
                            accumulated_placement[ref] = pose
                        for ref, side in (fallback_diag.get("sides") or {}).items():
                            accumulated_sides[ref] = side
                        overflow_refs = sorted(
                            ref for ref in fallback_local
                            if accumulated_sides.get(ref, "top") == "bottom"
                        )
                        overflow_note = (
                            f" ({len(overflow_refs)} to bottom: {overflow_refs})"
                            if overflow_refs else ""
                        )
                        print(
                            f"  Cluster C{cid}: deterministic fallback placed "
                            f"{len(cluster_refs)} parts{overflow_note}"
                        )
                        continue

                    if allow_bottom and not fallback_success:
                        # Global relief re-seed: the spread-friendly seeding
                        # score can starve a late large part — smaller parts
                        # scattered across the board leave no contiguous
                        # window big enough. Re-seed every movable ref on the
                        # full board, largest first, so small parts best-fit
                        # around the big ones instead of boxing them out.
                        # Least flexible first: THT parts have no bottom-side
                        # escape (their pins pierce the board), so they claim
                        # slots before size-sorted SMDs, which can still
                        # overflow to the bottom.
                        reseed_refs = sorted(
                            expected_movable_refs,
                            key=lambda ref: (
                                0 if ref not in bottom_eligible_refs else 1,
                                -(float(sizes.get(ref, (3.0, 3.0))[0])
                                  * float(sizes.get(ref, (3.0, 3.0))[1])),
                                str(ref),
                            ),
                        )
                        full_region = board_inner_region(
                            board_w, board_h, outline_clearance, movable_edge_keepout
                        )
                        reseed_place, reseed_ok, _reseed_region, reseed_diag = (
                            seed_components_in_region(
                                reseed_refs, full_region, sizes, {}, holes, keepouts,
                                component_clearance, outline_clearance,
                                board_w=board_w, board_h=board_h,
                                edge_keepout_margin=movable_edge_keepout,
                                ordered_refs=reseed_refs,
                                allow_bottom=True,
                                bottom_refs=bottom_eligible_refs,
                                tht_refs=tht_locked_refs,
                                forced_bottom_refs=locked_bottom_refs,
                            )
                        )
                        if reseed_ok and len(reseed_place) == len(reseed_refs):
                            accumulated_placement = dict(reseed_place)
                            accumulated_sides = dict(reseed_diag.get("sides") or {})
                            reseed_bottom = sorted(
                                ref for ref, side in accumulated_sides.items()
                                if side == "bottom"
                            )
                            print(
                                f"  Cluster C{cid}: relief re-seed placed all "
                                f"{len(reseed_refs)} movable parts largest-first "
                                f"({len(reseed_bottom)} to bottom: {reseed_bottom})"
                            )
                            continue
                        print(
                            f"    Relief re-seed incomplete: placed "
                            f"{len(reseed_place)}/{len(reseed_refs)}, "
                            f"failed={reseed_diag.get('failed_refs')}"
                        )

                    cluster_graph = G_optimization.subgraph(cluster_refs).copy()
                    occupied_keepouts = keepouts + placement_keepouts(
                        accumulated_placement, sizes, component_clearance
                    )
                    cluster_seed_positions = {
                        ref: [pose[0], pose[1]]
                        for ref, pose in fallback_local.items()
                    }
                    if not cluster_seed_positions:
                        cluster_seed_positions = {
                            ref: [cluster_regions[cid]["x"], cluster_regions[cid]["y"]]
                            for ref in cluster_refs
                        }

                    rescue_rotations = {ref: 0.0 for ref in cluster_refs}
                    rescue_xy = force_directed_placement(
                        cluster_graph, board_w, board_h, sizes,
                        clusters={cid: cluster_refs},
                        iterations=120 + 40 * (total_attempt_idx - 1),
                        seed=42 + total_attempt_idx,
                        edge_clearance=outline_clearance,
                        holes=holes,
                        keepouts=occupied_keepouts,
                        frozen_components=set(),
                        initial_positions=cluster_seed_positions,
                        component_clearance=component_clearance,
                        rotations=rescue_rotations,
                        edge_keepout_margin=movable_edge_keepout,
                    )
                    rescue_with_rot = {
                        ref: (coord[0], coord[1], rescue_rotations.get(ref, 0.0))
                        for ref, coord in rescue_xy.items()
                    }
                    combined_rescue = dict(accumulated_placement)
                    combined_rescue.update(rescue_with_rot)
                    rescue_bad = detect_issues(
                        combined_rescue, sizes, board_w, board_h, holes, keepouts,
                        edge_clearance=outline_clearance,
                        comp_clearance=component_clearance,
                        edge_keepout_margin=movable_edge_keepout,
                        sides=accumulated_sides or None,
                        tht_refs=tht_locked_refs,
                    )

                    if any(ref in set(cluster_refs) for ref in rescue_bad):
                        failed_refs = sorted(set(cluster_refs) & set(rescue_bad))
                        failed_clusters.append((cid, failed_refs))
                        print(
                            f"  Cluster C{cid}: rescue rejected due to hard violations for "
                            f"{failed_refs}"
                        )
                    else:
                        for ref, pose in rescue_with_rot.items():
                            accumulated_placement[ref] = pose
                        print(
                            f"  Cluster C{cid}: force-rescue placed {len(cluster_refs)} parts"
                        )

                # Side map from seeding overflow (None keeps legacy side-blind costs).
                attempt_sides = (
                    dict(accumulated_sides)
                    if allow_bottom and (
                        accumulated_sides or locked_top_refs or locked_bottom_refs
                    ) else None
                )
                # Hint-locked sides hold through every downstream cost/gate.
                if attempt_sides is not None:
                    for ref in locked_top_refs:
                        attempt_sides[ref] = "top"
                    for ref in locked_bottom_refs:
                        attempt_sides[ref] = "bottom"

                if accumulated_placement:
                    attempt_best_rl = {
                        ref: [pose[0], pose[1], pose[2]]
                        for ref, pose in accumulated_placement.items()
                        if ref in G_optimization.nodes()
                    }
                    attempt_best_rl_cost = compute_total_cost(
                        attempt_best_rl, G_optimization, sizes, board_w, board_h, holes, keepouts,
                        outline_clearance=outline_clearance, component_clearance=component_clearance,
                        edge_keepout_margin=movable_edge_keepout,
                        sides=attempt_sides, tht_refs=tht_locked_refs
                    )
                else:
                    print("  ⚠️ Cluster-first placement produced no positions.")
                    attempt_best_rl = {}
                    attempt_best_rl_cost = float("inf")

                print(f"  RL Best Placement Cost: {attempt_best_rl_cost:.1f}")

                print("\n[Step 6] Running mathematical issue detector...")
                if not attempt_best_rl:
                    print("  ⚠️ No RL placement generated. Using empty placement.")
                    bad_refs = []
                else:
                    bad_refs = detect_issues(
                        attempt_best_rl, sizes, board_w, board_h, holes, keepouts,
                        edge_clearance=outline_clearance, comp_clearance=component_clearance,
                        edge_keepout_margin=movable_edge_keepout,
                        sides=attempt_sides, tht_refs=tht_locked_refs
                    )
                print(f"  Detected {len(bad_refs)} boundary/overlap violations")

                fallback_seed_placement = {r: [c[0], c[1]] for r, c in attempt_best_rl.items()}
                attempt_rotations = {r: c[2] for r, c in attempt_best_rl.items()}
                relaxed_refs = set(bad_refs)
                for ref in bad_refs:
                    if ref in G_optimization:
                        relaxed_refs.update(G_optimization.neighbors(ref))
                    for cid, refs in clusters.items():
                        if ref in refs:
                            relaxed_refs.update(refs)
                            break
                frozen_set = set(attempt_best_rl.keys()) - relaxed_refs

                if len(bad_refs) > 0:
                    print("\n[Step 6.5] Initiating targeted force-directed micro-push fallback...")
                    repaired_placement = force_directed_placement(
                        G_optimization, board_w, board_h, sizes, clusters,
                        iterations=50 + 20 * (total_attempt_idx - 1),
                        seed=142 + total_attempt_idx,
                        edge_clearance=outline_clearance, holes=holes, keepouts=keepouts,
                        frozen_components=frozen_set, initial_positions=fallback_seed_placement,
                        component_clearance=component_clearance, rotations=attempt_rotations,
                        edge_keepout_margin=movable_edge_keepout
                    )
                else:
                    repaired_placement = fallback_seed_placement

                constraint_fixed = {
                    ref: {"x": props["x"], "y": props["y"],
                          "rotation": props.get("rotation", 0.0), "side": "top"}
                    for ref, props in fixed_connector_positions.items()
                }
                if relative_constraints:
                    combined_seed = dict(repaired_placement)
                    combined_seed.update({
                        ref: [pose["x"], pose["y"]]
                        for ref, pose in constraint_fixed.items()
                    })
                    seed_rotations = dict(attempt_rotations)
                    seed_rotations.update({
                        ref: pose["rotation"] for ref, pose in constraint_fixed.items()
                    })
                    seed_sides = dict(attempt_sides or {})
                    seed_sides.update({ref: "top" for ref in constraint_fixed})
                    seeded, seed_rotations, _seed_sides, seed_changes = seed_constraints(
                        combined_seed, seed_rotations, seed_sides,
                        relative_constraints, footprint_mapping, sizes,
                        board_w, board_h,
                        fixed_refs=set(fixed_connector_positions),
                        outline_clearance=outline_clearance,
                    )
                    repaired_placement = {
                        ref: seeded[ref] for ref in repaired_placement if ref in seeded
                    }
                    attempt_rotations.update({
                        ref: rotation for ref, rotation in seed_rotations.items()
                        if ref in repaired_placement
                    })
                    for change in seed_changes:
                        print(f"  [Constraint seed] {change}")

                repaired_cost = compute_total_cost(
                    attach_rotations(repaired_placement, attempt_rotations),
                    G_optimization, sizes, board_w, board_h, holes, keepouts,
                    outline_clearance=outline_clearance, component_clearance=component_clearance,
                    edge_keepout_margin=movable_edge_keepout,
                    sides=attempt_sides, tht_refs=tht_locked_refs,
                    relative_constraints=relative_constraints,
                    footprint_metadata=footprint_mapping,
                    constraint_fixed_placement=constraint_fixed,
                )
                print(f"  Repaired Placement Cost: {repaired_cost:.1f}")

                print(f"\n[Step 7] Running Restricted Micro-GA Refinement ({ga_generations} gens)...")
                attempt_optimized = optimize(
                    repaired_placement, G_optimization, sizes,
                    board_w, board_h, holes, keepouts,
                    population_size=ga_population,
                    generations=ga_generations,
                    mutation_sigma=0.5,
                    seed=242 + total_attempt_idx,
                    outline_clearance=outline_clearance,
                    component_clearance=component_clearance,
                    micro_mode=True,
                    rotations=attempt_rotations,
                    edge_keepout_margin=movable_edge_keepout,
                    sides=attempt_sides, tht_refs=tht_locked_refs,
                    block_pairs=[
                        (blocks_by_name[a], blocks_by_name[b])
                        for a, b in separation_pairs
                    ] or None,
                    block_separation_distance=separation_distance,
                    relative_constraints=relative_constraints,
                    footprint_metadata=footprint_mapping,
                    constraint_fixed_placement=constraint_fixed,
                )

                attempt_final_cost = compute_total_cost(
                    attach_rotations(attempt_optimized, attempt_rotations),
                    G_optimization, sizes, board_w, board_h, holes, keepouts,
                    outline_clearance=outline_clearance, component_clearance=component_clearance,
                    edge_keepout_margin=movable_edge_keepout,
                    sides=attempt_sides, tht_refs=tht_locked_refs,
                    block_pairs=[
                        (blocks_by_name[a], blocks_by_name[b])
                        for a, b in separation_pairs
                    ] or None,
                    block_separation_distance=separation_distance,
                    relative_constraints=relative_constraints,
                    footprint_metadata=footprint_mapping,
                    constraint_fixed_placement=constraint_fixed,
                )
                print(f"  Final Micro-GA placement cost: {attempt_final_cost:.1f}")

                _, missing_refs, final_bad_refs = assess_attempt(
                    attempt_optimized, attempt_rotations, expected_movable_refs, sizes,
                    board_w, board_h, holes, keepouts, outline_clearance,
                    component_clearance, movable_edge_keepout,
                    sides=attempt_sides, tht_refs=tht_locked_refs,
                    footprint_metadata=footprint_mapping,
                )

                if relative_constraints and not missing_refs:
                    candidate_for_constraints = {
                        ref: {
                            "x": coords[0], "y": coords[1],
                            "rotation": attempt_rotations.get(ref, 0.0),
                            "side": (attempt_sides or {}).get(ref, "top"),
                        }
                        for ref, coords in attempt_optimized.items()
                    }
                    candidate_for_constraints.update(constraint_fixed)
                    attempt_constraint_report = evaluate_constraints(
                        candidate_for_constraints, relative_constraints,
                        footprint_mapping, sizes, board_w, board_h,
                    )
                    failed_constraints = failed_hard_constraints(attempt_constraint_report)
                    if failed_constraints:
                        final_bad_refs = sorted(set(final_bad_refs) | {
                            ref for report in failed_constraints for ref in report["refs"]
                        })
                        for report in failed_constraints:
                            print(
                                f"  [Constraint violation] {report['id']}: "
                                f"actual={report['actual']} required={report['required']}"
                            )

                if not missing_refs and not final_bad_refs:
                    best_rl_placement = attempt_best_rl
                    best_rl_cost = attempt_best_rl_cost
                    component_rotations = attempt_rotations
                    optimized_placement = attempt_optimized
                    final_cost = attempt_final_cost
                    best_attempt_sides = dict(attempt_sides or {})
                    placement_succeeded = True
                    print(
                        f"  ✅ Attempt batch succeeded on board stage {resize_round_idx + 1}."
                    )
                    break

                last_missing_refs = missing_refs
                last_bad_refs = final_bad_refs
                last_failed_clusters = failed_clusters
                for failed_cid, failed_refs in failed_clusters:
                    batch_rejected_counts[failed_cid] = batch_rejected_counts.get(failed_cid, 0) + 1
                    batch_rejected_refs.setdefault(failed_cid, set()).update(failed_refs)
                print(
                    f"  ⚠️ Attempt {board_attempt_idx} in stage {resize_round_idx + 1} incomplete: "
                    f"missing={missing_refs}, hard_violations={final_bad_refs}"
                )

            if placement_succeeded:
                break

            if pending_resize is None and board_config.get("auto_resize_allowed", True):
                repeated_rejection_candidates = [
                    (
                        count,
                        float(cluster_info.get(cid, {}).get("bbox_area", 0.0)),
                        cid,
                    )
                    for cid, count in batch_rejected_counts.items()
                    if count >= attempts_per_board_size
                ]
                if repeated_rejection_candidates:
                    _, _, repeated_cluster_id = max(repeated_rejection_candidates)
                    pending_resize = build_rejection_pending_resize(
                        board_w,
                        board_h,
                        cluster_info,
                        repeated_cluster_id,
                        outline_clearance,
                        movable_edge_keepout,
                        component_clearance,
                    )
                    if pending_resize is not None:
                        repeated_refs = sorted(batch_rejected_refs.get(repeated_cluster_id, set()))
                        print(
                            "  No geometric resize target was pending, but the same cluster "
                            "was rejected across the full batch."
                        )
                        print(
                            f"  Triggering staged growth for cluster C{repeated_cluster_id}: "
                            f"{repeated_refs}"
                        )

            if pending_resize is None and board_config.get("auto_resize_allowed", True):
                pending_resize = build_batch_failure_pending_resize(
                    board_w,
                    board_h,
                    cluster_info,
                    last_missing_refs,
                    last_failed_clusters,
                    outline_clearance,
                    movable_edge_keepout,
                )
                if pending_resize is not None:
                    print(
                        "  No exact geometric resize target was pending after the failed "
                        "batch."
                    )
                    print(
                        "  Triggering low-step fallback board growth so placement can keep "
                        "trying instead of stopping."
                    )

            if not board_config.get("auto_resize_allowed", True) or pending_resize is None:
                break
            if max_resize_rounds is not None and resize_round_idx >= max_resize_rounds:
                break

            staged_board_w, staged_board_h, staged_reasons = next_staged_board_resize(
                current_w=board_w,
                current_h=board_h,
                target_w=pending_resize["board_w"],
                target_h=pending_resize["board_h"],
            )
            if staged_board_w <= board_w + 1e-6 and staged_board_h <= board_h + 1e-6:
                pending_resize = None
                break

            if max_resize_rounds is None:
                resize_header = f"\n[Board Resize {resize_round_idx + 1}] "
            else:
                resize_header = (
                    f"\n[Board Resize {resize_round_idx + 1}/{max_resize_rounds}] "
                )
            print(
                resize_header +
                "Exhausted current board-size batch; growing board one step..."
            )
            (
                board_w,
                board_h,
                fixed_connector_positions,
                fixed_keepouts,
                holes,
                mechanical_keepouts,
                connector_keepouts,
                keepouts,
                mechanical_config,
            ) = apply_board_resize(
                board_w,
                board_h,
                staged_board_w,
                staged_board_h,
                staged_reasons or pending_resize["reasons"],
                pending_resize["required_inner_w"],
                pending_resize["required_inner_h"],
                board_config,
                board_json_path,
                placer,
                pcb_obj,
                fixed_connector_positions,
                fixed_keepouts,
                holes,
                mechanical_keepouts,
                connector_keepouts,
                sizes,
                component_clearance,
                mechanical_config,
                mechanical_json_path,
            )
            resize_round_idx += 1

            clusters = split_dense_clusters(
                G_optimization,
                base_clusters,
                sizes,
                board_w,
                board_h,
                outline_clearance,
                movable_edge_keepout,
                component_clearance,
            )
            if cluster_hint_blocks:
                clusters = merge_llm_blocks(clusters, cluster_hint_blocks)
            if len(clusters) != initial_cluster_count:
                print(
                    f"  Updated dense-cluster split pass: {initial_cluster_count} -> "
                    f"{len(clusters)} clusters for board {board_w:.2f} x {board_h:.2f} mm"
                )
            cluster_info = analyze_clusters(
                G, G_optimization, clusters, sizes, pcb_obj.components,
                fixed_connector_positions, board_w, board_h, holes, keepouts,
                component_clearance, outline_clearance
            )
            center_cluster_id = choose_center_cluster(cluster_info)
            cluster_order = order_clusters(cluster_info, center_cluster_id)
            if cluster_order:
                print(f"  Updated center cluster after resize: C{center_cluster_id}")
                for row in summarize_cluster_table(
                    cluster_info, cluster_order, center_cluster_id
                ):
                    print(row)

            pending_resize = build_pending_resize(
                board_w,
                board_h,
                sizes,
                cluster_info,
                outline_clearance,
                movable_edge_keepout,
                component_clearance,
            )
            if pending_resize is not None:
                print(
                    f"  Remaining staged resize target: {pending_resize['board_w']:.2f} x "
                    f"{pending_resize['board_h']:.2f} mm"
                )

        if not placement_succeeded:
            if max_resize_rounds is None:
                resize_stage_msg = f"{resize_round_idx + 1} board-size stages"
            else:
                resize_stage_msg = (
                    f"{resize_round_idx + 1} board-size stages "
                    f"(limit {max_resize_rounds})"
                )
            raise RuntimeError(
                f"Placement failed after {total_attempt_idx} attempts across "
                f"{resize_stage_msg}. "
                f"Missing refs: {last_missing_refs}. "
                f"Hard-violating refs: {last_bad_refs}. "
                f"Rejected clusters: {last_failed_clusters}."
            )

    # --- Step 7.5: Side assignment (optional bottom-side placement) ---
    sides = {}
    if allow_bottom:
        print("\n[Step 7.5] Side assignment (bottom-side placement enabled)...")
        hint_forced_sides = {}
        for ref in locked_top_refs:
            hint_forced_sides[ref] = "top"
        for ref in locked_bottom_refs:
            hint_forced_sides[ref] = "bottom"
        sides, side_stats = assign_sides(
            optimized_placement, G_optimization, sizes, component_rotations,
            board_w, board_h, holes, keepouts, footprint_mapping,
            outline_clearance=outline_clearance,
            component_clearance=component_clearance,
            edge_keepout_margin=movable_edge_keepout,
            fixed_refs=fixed_connector_positions.keys(),
            bottom_bias=bottom_bias,
            initial_sides=best_attempt_sides,
            locked_refs=(locked_top_refs | locked_bottom_refs) or None,
            forced_sides=hint_forced_sides or None,
            per_ref_bottom_bias=prefer_top_weights or None,
        )
        print(f"  THT/connector refs locked to top: {len(side_stats['tht_refs'])}")
        print(f"  Bottom-side candidates: {len(side_stats['candidates'])}")
        if side_stats.get("locked_refs"):
            print(f"  [Side hints] locked (never toggled): {side_stats['locked_refs']}")
        if side_stats['initial_bottom']:
            print(
                f"  Seeded from placement overflow: {len(side_stats['initial_bottom'])} "
                f"ref(s) started on bottom: {side_stats['initial_bottom']}"
            )
        print(
            f"  Side pass moved {len(side_stats['flipped'])} part(s): "
            f"{side_stats['flipped']} (cost {side_stats['cost_before']:.2f} -> "
            f"{side_stats['cost_after']:.2f})"
        )

    # --- Step 8: Format and save output ---
    print("\n[Step 8] Saving placement JSON...")

    # Merge optimized positions with fixed connector positions
    output = {"placement": {}}

    # First add non-connector components (from GA optimization)
    for ref, (x, y) in optimized_placement.items():
        # Side comes from the Step 7.5 pass; absent/THT refs stay on top.
        side = sides.get(ref, "top")
        if ref in G_optimization.nodes():
            pkg = G_optimization.nodes[ref].get("package_type", "SMD")
            if pkg == "THT":
                side = "top"
        # Belt-and-braces clamps: hint-locked sides survive every path,
        # including runs where Step 7.5 saw no candidates at all.
        if ref in locked_top_refs:
            side = "top"
        if ref in locked_bottom_refs:
            side = "bottom"
        # GA optimizes XY only; retain the rotation map selected during the
        # accepted attempt (including opt-in constraint rotation candidates).
        inherited_rot = component_rotations.get(ref, 0.0)
            
        output["placement"][ref] = {"x": x, "y": y, "rotation": inherited_rot, "side": side}

    # Then add fixed connector positions (rule-based, not optimized)
    for ref, props in fixed_connector_positions.items():
        pkg = G.nodes[ref].get("package_type", "SMD") if ref in G.nodes() else "THT"
        # Connectors are user-facing: always top (the side pass never flips them).
        side = "top"
        output["placement"][ref] = {"x": props["x"], "y": props["y"], "rotation": props["rotation"], "side": side}
        print(f"    Added fixed connector: {ref} at ({props['x']:.1f}, {props['y']:.1f}) @ {props['rotation']}deg")

    # Routing-aware final placement pass.  It uses transformed pad/courtyard
    # geometry and a sparse coarse-channel test to identify failures before the
    # exact router.  Only short endpoint moves/rotations that strictly improve
    # routability and preserve every hard placement constraint are committed.
    print("\n[Placement route gate] pad escape + coarse channel audit...")
    try:
        repaired, route_report = repair_route_feasibility(
            input_dir, output["placement"], footprint_mapping, sizes,
            board_w, board_h, drc_config, layer_config,
            fixed_refs=fixed_connector_positions.keys(),
            relative_constraints=relative_constraints,
        )
        output["placement"] = repaired
        output["route_feasibility"] = route_report
        initial_failed = route_report["initial"]["failed_count"]
        final_failed = route_report["final"]["failed_count"]
        print(f"  coarse failures: {initial_failed} -> {final_failed}; "
              f"accepted repairs: {len(route_report['repairs'])}")
        for repair in route_report["repairs"]:
            print(f"  repaired {repair['ref']}: {repair['before']} -> {repair['after']}")
        if final_failed:
            print("  [WARN] Coarse route failures remain; exact routing will proceed and "
                  "may produce a PARTIAL board.")
    except Exception as exc:
        output["route_feasibility"] = {"audit_error": str(exc), "repairs": []}
        print(f"  [WARN] Routing-aware placement audit could not complete: {exc}")

    if separation_pairs:
        # Final report for the LLM: are the separate-hint blocks actually apart?
        print("\n[Cluster hints] final block separation:")
        final_out = output["placement"]
        for name_a, name_b in separation_pairs:
            pts_a = [
                (final_out[ref]["x"], final_out[ref]["y"])
                for ref in blocks_by_name[name_a] if ref in final_out
            ]
            pts_b = [
                (final_out[ref]["x"], final_out[ref]["y"])
                for ref in blocks_by_name[name_b] if ref in final_out
            ]
            if not pts_a or not pts_b:
                continue
            centroid_a = (sum(p[0] for p in pts_a) / len(pts_a),
                          sum(p[1] for p in pts_a) / len(pts_a))
            centroid_b = (sum(p[0] for p in pts_b) / len(pts_b),
                          sum(p[1] for p in pts_b) / len(pts_b))
            dist = ((centroid_a[0] - centroid_b[0]) ** 2
                    + (centroid_a[1] - centroid_b[1]) ** 2) ** 0.5
            # Judge at the 0.1 mm precision the report displays, so a converged
            # boundary (e.g. 5.996 vs 6.0) doesn't read "6.0 mm ... BELOW TARGET".
            status = "OK" if round(dist, 1) >= separation_distance else "BELOW TARGET"
            print(
                f"  {name_a} <-> {name_b}: {dist:.1f} mm "
                f"(required >= {separation_distance:.1f} mm) {status}"
            )

    hard_constraint_failures = []
    if relative_constraints:
        constraint_report = evaluate_constraints(
            output["placement"], relative_constraints, footprint_mapping, sizes,
            board_w, board_h,
        )
        output["constraint_report"] = constraint_report
        hard_constraint_failures = failed_hard_constraints(constraint_report)
        print("\n[Placement constraints] final geometry-aware validation:")
        for report in constraint_report:
            status = "OK" if report["satisfied"] else "VIOLATION"
            print(
                f"  {report['id']} ({report['type']}): {status}; "
                f"actual={report['actual']} required={report['required']}"
            )

    output_path = os.path.join(input_dir, f"{prefix}_placement.json")
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"  Saved to: {output_path}")
    if hard_constraint_failures:
        details = "; ".join(
            f"{report['id']} actual={report['actual']} required={report['required']}"
            for report in hard_constraint_failures
        )
        raise RuntimeError(
            "Hard geometry-aware placement constraints were not satisfied: " + details
        )
    print("\n" + "=" * 60)
    print("  Pipeline complete!")
    print("=" * 60)

    return output


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python main_pipeline.py <input_dir> <prefix> [generations] [population]")
        sys.exit(1)

    input_dir = sys.argv[1]
    prefix = sys.argv[2]
    gens = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    pop = int(sys.argv[4]) if len(sys.argv) > 4 else 50

    run_pipeline(input_dir, prefix, ga_generations=gens, ga_population=pop)
