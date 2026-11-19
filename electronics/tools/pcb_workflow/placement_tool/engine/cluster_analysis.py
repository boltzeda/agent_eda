#!/usr/bin/env python3
"""
Cluster scoring and ordering helpers for hierarchical placement.
"""

import math
from collections import defaultdict

from geometry_utils import coerce_size, hole_radius, keepout_bbox


CLOCK_KEYWORDS = ("CLK", "SCK", "XTAL", "OSC", "CLOCK")
EDGE_NAMES = ("left", "right", "top", "bottom")


def _enum_name(value, default=""):
    """Return enum-like object names safely."""
    if value is None:
        return default
    return getattr(value, "name", str(value))


def _component_area(ref, sizes):
    width, height = coerce_size(sizes.get(ref, (3.0, 3.0)))
    return width * height


def _component_flags(ref, G_opt, pcb_components):
    node = G_opt.nodes.get(ref, {})
    prefix = node.get("prefix", "")
    package_type = node.get("package_type", "SMD")
    comp = pcb_components.get(ref)
    semantic_name = _enum_name(getattr(comp, "semantic_class", None), "")
    interface_name = _enum_name(getattr(comp, "interface_type", None), "")
    return {
        "prefix": prefix,
        "package_type": package_type,
        "semantic_name": semantic_name,
        "interface_name": interface_name,
    }


def _pack_slots(slot_dims, target_width):
    """Pack heterogeneous component slots into rows near a target width."""
    rows = []
    row_width = 0.0
    row_height = 0.0
    row_count = 0

    for slot_w, slot_h in slot_dims:
        proposed_width = slot_w if row_count == 0 else row_width + slot_w
        if row_count > 0 and proposed_width > target_width:
            rows.append((row_width, row_height, row_count))
            row_width = slot_w
            row_height = slot_h
            row_count = 1
            continue

        row_width = proposed_width
        row_height = max(row_height, slot_h)
        row_count += 1

    if row_count > 0:
        rows.append((row_width, row_height, row_count))

    packed_w = max((row[0] for row in rows), default=0.0)
    packed_h = sum(row[1] for row in rows)
    return packed_w, packed_h, len(rows)


def estimate_cluster_envelope(refs, sizes, component_clearance,
                              aspect_hint=1.0, cluster_margin=None):
    """Estimate a cluster bbox using mixed-size row packing instead of max-size slots."""
    refs = list(refs or [])
    if not refs:
        return {
            "bbox_w": 0.0,
            "bbox_h": 0.0,
            "bbox_area": 0.0,
            "packed_fill": 1.0,
            "row_count": 0,
            "slot_area": 0.0,
        }

    cluster_margin = max(2.0, 2.0 * component_clearance) if cluster_margin is None else float(cluster_margin)
    clearance_pad = max(float(component_clearance), 0.8)
    slot_dims = []
    max_w = 0.0
    max_h = 0.0
    slot_area = 0.0

    for ref in refs:
        width, height = coerce_size(sizes.get(ref, (3.0, 3.0)))
        max_w = max(max_w, width)
        max_h = max(max_h, height)
        slot_w = width + clearance_pad
        slot_h = height + clearance_pad
        slot_dims.append((slot_w, slot_h))
        slot_area += slot_w * slot_h

    slot_dims.sort(key=lambda dims: (max(dims[0], dims[1]), dims[0] * dims[1]), reverse=True)
    target_fill = 0.62 if len(refs) <= 2 else 0.57 if len(refs) <= 5 else 0.52
    base_width = max(
        max((dims[0] for dims in slot_dims), default=0.0),
        math.sqrt(slot_area / max(target_fill, 1e-6) * max(float(aspect_hint), 1e-6)),
    )

    candidate_widths = [
        max((dims[0] for dims in slot_dims), default=0.0),
        0.82 * base_width,
        base_width,
        1.18 * base_width,
    ]
    seen = set()
    candidates = []
    for width in candidate_widths:
        rounded = round(max(width, 1.0), 4)
        if rounded not in seen:
            seen.add(rounded)
            candidates.append(rounded)

    best = None
    best_score = None
    target_aspect = max(float(aspect_hint), 1e-6)
    for candidate_width in candidates:
        packed_w, packed_h, row_count = _pack_slots(slot_dims, candidate_width)
        packed_area = max(packed_w * packed_h, 1e-6)
        aspect = packed_w / max(packed_h, 1e-6)
        aspect_penalty = abs(math.log(max(aspect, 1e-6) / target_aspect))
        row_penalty = max(row_count - 1, 0) / max(len(refs), 1)
        score = packed_area * (1.0 + 0.20 * aspect_penalty + 0.08 * row_penalty)
        if best_score is None or score < best_score:
            best_score = score
            best = (packed_w, packed_h, row_count)

    packed_w, packed_h, row_count = best
    bbox_w = max(
        packed_w + 2.0 * cluster_margin,
        max_w + 2.0 * component_clearance + 2.0 * cluster_margin,
    )
    bbox_h = max(
        packed_h + 2.0 * cluster_margin,
        max_h + 2.0 * component_clearance + 2.0 * cluster_margin,
    )
    packed_fill = min(1.0, slot_area / max(packed_w * packed_h, 1e-6))

    return {
        "bbox_w": bbox_w,
        "bbox_h": bbox_h,
        "bbox_area": bbox_w * bbox_h,
        "packed_fill": packed_fill,
        "row_count": row_count,
        "slot_area": slot_area,
    }


def build_cluster_lookup(clusters):
    """Return ref -> cluster id lookup."""
    lookup = {}
    for cid, refs in clusters.items():
        for ref in refs:
            lookup[ref] = cid
    return lookup


def infer_fixed_connector_edges(fixed_connector_positions, board_w, board_h):
    """Infer the closest board edge for each fixed semantic component."""
    inferred = {}
    for ref, props in fixed_connector_positions.items():
        x = float(props.get("x", 0.0))
        y = float(props.get("y", 0.0))
        distances = {
            "left": x,
            "right": max(board_w - x, 0.0),
            "bottom": y,
            "top": max(board_h - y, 0.0),
        }
        inferred[ref] = min(distances, key=distances.get)
    return inferred


def compute_cluster_links(G_full, G_opt, clusters, fixed_connector_edges):
    """Compute cluster-to-cluster and cluster-to-fixed weights."""
    cluster_of = build_cluster_lookup(clusters)
    intercluster_links = {cid: defaultdict(float) for cid in clusters}
    connector_links = {cid: defaultdict(float) for cid in clusters}
    connector_edge_weights = {
        cid: {edge: 0.0 for edge in EDGE_NAMES}
        for cid in clusters
    }

    for u, v, data in G_opt.edges(data=True):
        cid_u = cluster_of.get(u)
        cid_v = cluster_of.get(v)
        if cid_u is None or cid_v is None or cid_u == cid_v:
            continue
        weight = float(data.get("weight", 1.0))
        intercluster_links[cid_u][cid_v] += weight
        intercluster_links[cid_v][cid_u] += weight

    fixed_refs = set(fixed_connector_edges.keys())
    for u, v, data in G_full.edges(data=True):
        weight = float(data.get("weight", 1.0))
        cid_u = cluster_of.get(u)
        cid_v = cluster_of.get(v)

        if cid_u is not None and v in fixed_refs:
            connector_links[cid_u][v] += weight
            connector_edge_weights[cid_u][fixed_connector_edges[v]] += weight
        if cid_v is not None and u in fixed_refs:
            connector_links[cid_v][u] += weight
            connector_edge_weights[cid_v][fixed_connector_edges[u]] += weight

    return intercluster_links, connector_links, connector_edge_weights


def analyze_clusters(
    G_full,
    G_opt,
    clusters,
    sizes,
    pcb_components,
    fixed_connector_positions,
    board_w,
    board_h,
    holes,
    keepouts,
    component_clearance,
    outline_clearance,
):
    """
    Compute cluster metadata, ranking scores, and placement roles.
    """
    fixed_connector_edges = infer_fixed_connector_edges(
        fixed_connector_positions, board_w, board_h
    )
    intercluster_links, connector_links, connector_edge_weights = compute_cluster_links(
        G_full, G_opt, clusters, fixed_connector_edges
    )

    obstacle_area = 0.0
    for keepout in keepouts or []:
        ko_box = keepout_bbox(keepout)
        if ko_box is None:
            continue
        obstacle_area += abs(ko_box[2] - ko_box[0]) * abs(ko_box[3] - ko_box[1])
    for hole in holes or []:
        diameter = float(hole.get("diameter", 2.0 * hole_radius(hole)))
        obstacle_area += (diameter + 2.0 * component_clearance) ** 2
    obstacle_ratio = obstacle_area / max(board_w * board_h, 1e-6)

    raw = {}
    max_bbox_area = 0.0
    max_intercluster_weight = 0.0
    max_connector_weight = 0.0
    max_clock_weight = 0.0

    for cid, refs in clusters.items():
        count_ic = 0
        count_passive = 0
        count_crystal = 0
        count_tht = 0
        has_mcu_fpga = 0
        has_memory = 0
        has_power_iface = 0
        has_rf_iface = 0
        has_ui_iface = 0
        has_safety_iface = 0

        area_raw = 0.0
        max_w = 0.0
        max_h = 0.0

        for ref in refs:
            width, height = coerce_size(sizes.get(ref, (3.0, 3.0)))
            area_raw += width * height
            max_w = max(max_w, width)
            max_h = max(max_h, height)

            flags = _component_flags(ref, G_opt, pcb_components)
            prefix = flags["prefix"]
            semantic_name = flags["semantic_name"]
            interface_name = flags["interface_name"]

            if prefix == "U":
                count_ic += 1
            if prefix in {"R", "C", "L", "D"}:
                count_passive += 1
            if prefix in {"Y", "X"}:
                count_crystal += 1
            if flags["package_type"] == "THT":
                count_tht += 1

            has_mcu_fpga = max(has_mcu_fpga, int(semantic_name == "MCU_FPGA"))
            has_memory = max(has_memory, int(semantic_name == "MEMORY"))
            has_power_iface = max(has_power_iface, int(interface_name == "POWER"))
            has_rf_iface = max(has_rf_iface, int(interface_name == "RF"))
            has_ui_iface = max(has_ui_iface, int(interface_name in {"USER", "IO"}))
            has_safety_iface = max(has_safety_iface, int(interface_name == "SAFETY"))

        pack_density = 0.68 if len(refs) <= 2 else 0.62 if len(refs) <= 5 else 0.58
        cluster_margin = max(2.0, 2.0 * component_clearance)
        provisional_edge_flag = int(has_power_iface or has_rf_iface)
        aspect_hint = 1.6 if provisional_edge_flag else 1.0
        analytic_bbox_area = area_raw / max(pack_density, 1e-6)
        analytic_bbox_w = max(
            max_w + 2.0 * component_clearance,
            (analytic_bbox_area * aspect_hint) ** 0.5,
        ) + 2.0 * cluster_margin
        analytic_bbox_h = max(
            max_h + 2.0 * component_clearance,
            (analytic_bbox_area / max(aspect_hint, 1e-6)) ** 0.5,
        ) + 2.0 * cluster_margin

        packed_envelope = estimate_cluster_envelope(
            refs,
            sizes,
            component_clearance,
            aspect_hint=aspect_hint,
            cluster_margin=cluster_margin,
        )
        bbox_w = max(analytic_bbox_w, packed_envelope["bbox_w"])
        bbox_h = max(analytic_bbox_h, packed_envelope["bbox_h"])
        bbox_area = max(analytic_bbox_area, bbox_w * bbox_h)

        member_norm = min(1.0, len(refs) / 8.0)
        passive_ratio = count_passive / max(len(refs), 1)
        row_pressure = min(1.0, packed_envelope["row_count"] / max(len(refs), 1))
        packing_pressure_seed = min(
            1.0,
            0.35 * member_norm
            + 0.25 * passive_ratio
            + 0.25 * (1.0 - packed_envelope["packed_fill"])
            + 0.15 * row_pressure,
        )
        growth_scale = 1.0 + 0.18 * packing_pressure_seed
        bbox_w *= growth_scale
        bbox_h *= growth_scale
        bbox_area = max(bbox_area, bbox_w * bbox_h)

        intercluster_weight_raw = sum(intercluster_links[cid].values())
        connector_link_weight_raw = sum(connector_links[cid].values())

        clock_weight_raw = 0.0
        ref_set = set(refs)
        for u, v, data in G_full.edges(data=True):
            if u not in ref_set and v not in ref_set:
                continue
            nets = data.get("nets", [])
            if any(
                keyword in str(net).upper()
                for net in nets
                for keyword in CLOCK_KEYWORDS
            ):
                clock_weight_raw += float(data.get("weight", 1.0))

        raw[cid] = {
            "cluster_id": int(cid),
            "refs": list(refs),
            "member_count": len(refs),
            "count_ic": count_ic,
            "count_passive": count_passive,
            "count_crystal": count_crystal,
            "count_tht": count_tht,
            "has_mcu_fpga": has_mcu_fpga,
            "has_memory": has_memory,
            "has_power_iface": has_power_iface,
            "has_rf_iface": has_rf_iface,
            "has_ui_iface": has_ui_iface,
            "has_safety_iface": has_safety_iface,
            "area_raw": area_raw,
            "bbox_w": bbox_w,
            "bbox_h": bbox_h,
            "bbox_area": bbox_area,
            "packing_pressure_seed": packing_pressure_seed,
            "packed_fill": packed_envelope["packed_fill"],
            "packed_rows": packed_envelope["row_count"],
            "slot_area": packed_envelope["slot_area"],
            "intercluster_links": dict(intercluster_links[cid]),
            "connector_links": dict(connector_links[cid]),
            "connector_edge_weights": dict(connector_edge_weights[cid]),
            "intercluster_weight_raw": intercluster_weight_raw,
            "connector_link_weight_raw": connector_link_weight_raw,
            "clock_weight_raw": clock_weight_raw,
        }

        max_bbox_area = max(max_bbox_area, bbox_area)
        max_intercluster_weight = max(max_intercluster_weight, intercluster_weight_raw)
        max_connector_weight = max(max_connector_weight, connector_link_weight_raw)
        max_clock_weight = max(max_clock_weight, clock_weight_raw)

    max_bbox_area = max(max_bbox_area, 1.0)
    max_intercluster_weight = max(max_intercluster_weight, 1.0)
    max_connector_weight = max(max_connector_weight, 1.0)
    max_clock_weight = max(max_clock_weight, 1.0)

    cluster_info = {}
    for cid, meta in raw.items():
        bbox_area_norm = meta["bbox_area"] / max_bbox_area
        intercluster_weight_norm = meta["intercluster_weight_raw"] / max_intercluster_weight
        connector_link_weight_norm = meta["connector_link_weight_raw"] / max_connector_weight
        clock_weight_norm = meta["clock_weight_raw"] / max_clock_weight

        main_ic_raw = (
            1.0 if meta["has_mcu_fpga"]
            else 0.8 if meta["has_memory"]
            else 0.6 if meta["count_ic"] > 0
            else 0.0
        )
        edge_semantic_raw = min(
            1.0,
            0.60 * meta["has_power_iface"]
            + 0.75 * meta["has_rf_iface"]
            + 0.45 * meta["has_ui_iface"]
            + 0.55 * meta["has_safety_iface"]
            + 0.20 * int(meta["count_tht"] > 0),
        )
        timing_criticality = min(
            1.0,
            0.60 * int(meta["count_crystal"] > 0) + 0.40 * clock_weight_norm,
        )
        mechanical_pressure = min(1.0, bbox_area_norm * (1.0 + obstacle_ratio))
        packing_pressure = min(
            1.0,
            0.55 * meta.get("packing_pressure_seed", 0.0) + 0.45 * mechanical_pressure,
        )
        edge_affinity = min(
            1.0,
            0.55 * edge_semantic_raw + 0.45 * connector_link_weight_norm,
        )

        center_score = (
            0.40 * intercluster_weight_norm
            + 0.25 * main_ic_raw
            + 0.20 * timing_criticality
            + 0.15 * bbox_area_norm
            - 0.35 * edge_affinity
        )
        first_score = (
            0.35 * edge_affinity
            + 0.25 * connector_link_weight_norm
            + 0.20 * mechanical_pressure
            + 0.15 * timing_criticality
            + 0.05 * bbox_area_norm
        )

        if edge_affinity >= 0.60:
            role = "edge_hard"
            order_group = 0
        elif main_ic_raw >= 0.80 and center_score >= 0.45:
            role = "center_hub"
            order_group = 1
        elif len(meta["refs"]) <= 2 and main_ic_raw == 0.0 and intercluster_weight_norm < 0.25:
            role = "filler"
            order_group = 3
        else:
            role = "support"
            order_group = 2

        preferred_edge = None
        edge_weights = meta["connector_edge_weights"]
        if any(weight > 0 for weight in edge_weights.values()):
            preferred_edge = max(edge_weights, key=edge_weights.get)
        elif role == "edge_hard":
            if meta["has_rf_iface"]:
                preferred_edge = "top"
            elif meta["has_power_iface"]:
                preferred_edge = "left"
            elif meta["has_ui_iface"]:
                preferred_edge = "bottom"

        preferred_region = "free"
        if role == "center_hub":
            preferred_region = "center"
        elif role == "edge_hard":
            preferred_region = "edge_band"
        elif role == "support":
            preferred_region = "ring"

        cluster_info[cid] = {
            **meta,
            "bbox_area_norm": bbox_area_norm,
            "intercluster_weight_norm": intercluster_weight_norm,
            "connector_link_weight_norm": connector_link_weight_norm,
            "clock_weight_norm": clock_weight_norm,
            "edge_semantic_raw": edge_semantic_raw,
            "edge_affinity": edge_affinity,
            "timing_criticality": timing_criticality,
            "main_ic_raw": main_ic_raw,
            "mechanical_pressure": mechanical_pressure,
            "packing_pressure": packing_pressure,
            "center_score": center_score,
            "first_score": first_score,
            "role": role,
            "preferred_edge": preferred_edge,
            "preferred_region": preferred_region,
            "order_group": order_group,
            "outline_clearance": outline_clearance,
        }

    return cluster_info


def choose_center_cluster(cluster_info):
    """Choose the preferred center cluster."""
    if not cluster_info:
        return None

    candidates = [
        cid for cid, meta in cluster_info.items()
        if meta["edge_affinity"] < 0.60
    ]
    if not candidates:
        candidates = list(cluster_info.keys())
    return max(candidates, key=lambda cid: cluster_info[cid]["center_score"])


def order_clusters(cluster_info, center_cluster_id):
    """Return cluster ids in hierarchical placement order."""
    edge_hard = [
        cid for cid, meta in cluster_info.items()
        if meta["role"] == "edge_hard"
    ]
    edge_hard.sort(key=lambda cid: (-cluster_info[cid]["first_score"], cid))

    support = [
        cid for cid, meta in cluster_info.items()
        if meta["role"] == "support" and cid != center_cluster_id
    ]
    support.sort(
        key=lambda cid: (
            -cluster_info[cid]["intercluster_weight_norm"],
            -cluster_info[cid]["center_score"],
            cid,
        )
    )

    filler = [
        cid for cid, meta in cluster_info.items()
        if meta["role"] == "filler" and cid != center_cluster_id
    ]
    filler.sort(key=lambda cid: (cluster_info[cid]["bbox_area"], cid))

    ordered = list(edge_hard)
    if center_cluster_id is not None and center_cluster_id not in ordered:
        ordered.append(center_cluster_id)
    ordered.extend(cid for cid in support if cid not in ordered)
    ordered.extend(cid for cid in filler if cid not in ordered)

    for cid in sorted(cluster_info):
        if cid not in ordered:
            ordered.append(cid)
    return ordered


def summarize_cluster_table(cluster_info, cluster_order, center_cluster_id):
    """Return short human-readable cluster summaries for pipeline logs."""
    rows = []
    order_index = {cid: idx for idx, cid in enumerate(cluster_order)}
    for cid in cluster_order:
        meta = cluster_info[cid]
        center_tag = " center" if cid == center_cluster_id else ""
        rows.append(
            "    "
            f"[{order_index[cid]:02d}] C{cid} {meta['role']}{center_tag}: "
            f"members={meta['member_count']}, "
            f"bbox={meta['bbox_w']:.1f}x{meta['bbox_h']:.1f}, "
            f"center={meta['center_score']:.2f}, "
            f"first={meta['first_score']:.2f}, "
            f"edge={meta['preferred_edge'] or '-'}"
        )
    return rows
