#!/usr/bin/env python3
"""
pcb_auto_placement/clustering.py

PCB-specific component clustering using domain rules:
  - Crystal Rule: crystals + load caps clustered with their IC
  - Passive Support Rule: passives grouped with the IC they connect to
  - Connector Rule: connectors form their own edge cluster
  - Power Net Rule: VCC/GND nets are excluded from clustering decisions
  - Subsystem Rule: tightly connected ICs form subsystem clusters
  - Cluster Size Rule: 2–12 components per cluster
"""

import networkx as nx
from cluster_analysis import (
    analyze_clusters,
    choose_center_cluster,
    estimate_cluster_envelope,
    order_clusters,
    summarize_cluster_table,
)

# Power net names to ignore during clustering
POWER_NET_NAMES = {
    "VCC", "VDD", "3V3", "3.3V", "5V", "12V", "VBUS", "VBAT",
    "GND", "AGND", "DGND", "VSS", "AVCC", "AVDD",
    "+3V3", "+5V", "+12V", "+3.3V", "V+", "V-",
    "Net-(unnamed)", "unconnected",
}

MAX_CLUSTER_SIZE = 12


def _is_power_net(net_name: str) -> bool:
    """Check if a net name is a power/ground net."""
    name_upper = net_name.upper().strip("/").strip()
    return name_upper in {n.upper() for n in POWER_NET_NAMES}


def _build_signal_graph(G: nx.Graph) -> nx.Graph:
    """
    Build a filtered graph that excludes power net edges.
    Only signal-level connections remain for clustering decisions.
    """
    SG = nx.Graph()
    SG.add_nodes_from(G.nodes(data=True))

    for u, v, data in G.edges(data=True):
        nets = data.get("nets", [])
        # Filter out power nets
        signal_nets = [n for n in nets if not _is_power_net(n)]
        if signal_nets:
            weight = len(signal_nets)
            SG.add_edge(u, v, weight=weight, nets=signal_nets)

    return SG


def _legal_inner_dims(board_w: float, board_h: float,
                      outline_clearance: float,
                      edge_keepout_margin: float) -> tuple[float, float]:
    effective_outline = float(outline_clearance) + max(float(edge_keepout_margin or 0.0), 0.0)
    inner_w = max(float(board_w) - 2.0 * effective_outline, 1.0)
    inner_h = max(float(board_h) - 2.0 * effective_outline, 1.0)
    return inner_w, inner_h


def _fallback_split_refs(refs: list[str], SG: nx.Graph) -> list[set[str]]:
    ranked = sorted(
        refs,
        key=lambda ref: (
            -(1 if SG.nodes.get(ref, {}).get("prefix", "") == "U" else 0),
            -SG.degree(ref),
            ref,
        ),
    )
    midpoint = max(1, len(ranked) // 2)
    left = set(ranked[:midpoint])
    right = set(ranked[midpoint:])
    return [group for group in (left, right) if group]


def _normalize_communities(communities: list[set[str]], SG: nx.Graph) -> list[set[str]]:
    groups = [set(group) for group in communities if group]
    if len(groups) < 2:
        return groups

    changed = True
    while changed and len(groups) > 1:
        changed = False
        for group in list(groups):
            if len(group) != 1:
                continue
            ref = next(iter(group))
            prefix = SG.nodes.get(ref, {}).get("prefix", "")
            if prefix in {"U", "Y", "X"}:
                continue

            best_target = None
            best_weight = 0.0
            for other in groups:
                if other is group:
                    continue
                weight = 0.0
                for other_ref in other:
                    if SG.has_edge(ref, other_ref):
                        weight += float(SG[ref][other_ref].get("weight", 1.0))
                if weight > best_weight:
                    best_weight = weight
                    best_target = other

            if best_target is not None and best_weight > 0.0:
                best_target.update(group)
                groups.remove(group)
                changed = True
                break

    return groups


def _partition_cluster(refs: list[str], SG: nx.Graph) -> list[set[str]]:
    subgraph = SG.subgraph(refs).copy()
    if len(subgraph.nodes()) < 2:
        return [set(refs)]

    if subgraph.number_of_edges() > 0:
        communities = [
            set(group)
            for group in nx.community.greedy_modularity_communities(
                subgraph, weight="weight"
            )
        ]
    else:
        communities = []

    if len(communities) < 2:
        communities = _fallback_split_refs(list(refs), subgraph)

    return _normalize_communities(communities, subgraph)


def _should_split_cluster(refs: list[str], G: nx.Graph, sizes: dict,
                          board_w: float, board_h: float,
                          outline_clearance: float, edge_keepout_margin: float,
                          component_clearance: float) -> bool:
    if len(refs) < 4:
        return False

    inner_w, inner_h = _legal_inner_dims(
        board_w, board_h, outline_clearance, edge_keepout_margin
    )
    envelope = estimate_cluster_envelope(
        refs,
        sizes,
        component_clearance,
        aspect_hint=1.0,
    )
    board_inner_area = max(inner_w * inner_h, 1e-6)
    area_ratio = envelope["bbox_area"] / board_inner_area
    span_ratio = max(
        envelope["bbox_w"] / max(inner_w, 1e-6),
        envelope["bbox_h"] / max(inner_h, 1e-6),
    )
    ic_count = sum(
        1 for ref in refs if G.nodes.get(ref, {}).get("prefix", "") == "U"
    )

    return (
        span_ratio > 0.86
        or area_ratio > 0.58
        or (len(refs) >= 8 and area_ratio > 0.46)
    ) and (ic_count >= 2 or len(refs) >= 8)


def split_dense_clusters(G: nx.Graph, clusters: dict, sizes: dict,
                         board_w: float, board_h: float,
                         outline_clearance: float, edge_keepout_margin: float,
                         component_clearance: float,
                         max_depth: int = 2) -> dict:
    """Split clusters that are too dense for the current legal board interior."""
    if not clusters:
        return {}

    SG = _build_signal_graph(G)
    split_clusters = []

    def split_recursive(refs: list[str], depth: int) -> None:
        refs = list(refs)
        if depth >= max_depth or not _should_split_cluster(
            refs,
            G,
            sizes,
            board_w,
            board_h,
            outline_clearance,
            edge_keepout_margin,
            component_clearance,
        ):
            split_clusters.append(refs)
            return

        communities = _partition_cluster(refs, SG)
        if len(communities) < 2:
            split_clusters.append(refs)
            return

        if sum(len(group) for group in communities) != len(set(refs)):
            split_clusters.append(refs)
            return

        for group in communities:
            split_recursive(sorted(group), depth + 1)

    for _, refs in sorted(clusters.items()):
        split_recursive(list(refs), 0)

    renumbered = {}
    for new_id, refs in enumerate(split_clusters):
        renumbered[new_id] = list(refs)
    return renumbered


def _find_crystals_and_ics(SG: nx.Graph) -> dict:
    """
    Find crystals and map each to its connected IC.
    Returns dict: {crystal_ref: ic_ref}
    """
    crystal_to_ic = {}
    for node in SG.nodes():
        is_crystal = SG.nodes[node].get("is_crystal", False)
        prefix = SG.nodes[node].get("prefix", "")
        if is_crystal or prefix in ("Y",):
            # Find the IC this crystal connects to
            neighbors = list(SG.neighbors(node))
            for neighbor in neighbors:
                n_prefix = SG.nodes[neighbor].get("prefix", "")
                if n_prefix in ("U",):  # IC prefixes
                    crystal_to_ic[node] = neighbor
                    break
            # If no IC found directly, pick the neighbor with most pins
            if node not in crystal_to_ic and neighbors:
                crystal_to_ic[node] = neighbors[0]

    return crystal_to_ic


def _find_decoupling_caps(SG: nx.Graph, G: nx.Graph) -> dict:
    """
    Identify decoupling capacitors and map each to its closest IC.
    A decoupling cap typically:
      - Has prefix 'C'
      - Has small value (pF or nF range, or 100nF/0.1uF)
      - Connects to an IC through a signal or power net

    Returns dict: {cap_ref: ic_ref}
    """
    cap_to_ic = {}

    for node in G.nodes():
        prefix = SG.nodes[node].get("prefix", "") if node in SG.nodes() else ""
        value = SG.nodes[node].get("value", "") if node in SG.nodes() else ""

        if prefix != "C":
            continue

        # Check if it's likely a decoupling/bypass cap
        val_lower = value.lower()
        is_decoupling = any(v in val_lower for v in [
            "100n", "0.1u", "100nf", "10n", "10nf", "1u", "1uf",
            "22p", "22pf", "15p", "15pf", "33p", "33pf",
            "4.7u", "4.7uf", "10u", "10uf",
        ])

        if not is_decoupling:
            # Still treat small caps as potential decoupling
            is_decoupling = any(unit in val_lower for unit in ["pf", "nf"])

        if is_decoupling:
            # Find the IC this cap connects to (via signal graph)
            if node in SG.nodes():
                for neighbor in SG.neighbors(node):
                    n_prefix = SG.nodes[neighbor].get("prefix", "")
                    if n_prefix in ("U",):
                        cap_to_ic[node] = neighbor
                        break

            # If not found in signal graph, check full graph
            if node not in cap_to_ic and node in G.nodes():
                for neighbor in G.neighbors(node):
                    n_prefix = G.nodes[neighbor].get("prefix", "") if neighbor in G.nodes() else ""
                    if n_prefix in ("U",):
                        cap_to_ic[node] = neighbor
                        break

    return cap_to_ic


def _find_passive_to_ic(SG: nx.Graph) -> dict:
    """
    Map passive components (R, C, D, L) to their primary IC.
    A passive belongs to the IC it has the strongest signal connection to.

    Returns dict: {passive_ref: ic_ref}
    """
    passive_to_ic = {}
    passive_prefixes = {"R", "C", "D", "L"}

    for node in SG.nodes():
        prefix = SG.nodes[node].get("prefix", "")
        if prefix not in passive_prefixes:
            continue

        # Find the IC neighbor with strongest connection
        best_ic = None
        best_weight = 0
        for neighbor in SG.neighbors(node):
            n_prefix = SG.nodes[neighbor].get("prefix", "")
            if n_prefix in ("U",):
                edge_weight = SG[node][neighbor].get("weight", 1)
                if edge_weight > best_weight:
                    best_weight = edge_weight
                    best_ic = neighbor

        if best_ic:
            passive_to_ic[node] = best_ic

    return passive_to_ic


def cluster_components(G: nx.Graph) -> dict:
    """
    PCB-specific clustering algorithm.

    Procedure:
      1. Filter out power nets to build a signal-only graph
      2. Identify connectors → connector cluster
      3. Identify crystals → cluster with their IC
      4. Identify decoupling caps → cluster with their IC
      5. Map remaining passives to their IC
      6. Group remaining ICs into subsystem clusters
      7. Assign any orphan components

    Returns:
        dict mapping cluster_id -> list of component refs
    """
    if len(G.nodes()) == 0:
        return {}

    # Step 1: Build signal-only graph (exclude power nets)
    SG = _build_signal_graph(G)

    # Track which components are already assigned
    assigned = {}  # ref -> cluster_id
    clusters = {}  # cluster_id -> [refs]
    next_cluster_id = 0

    # Step 2: Connector cluster
    connector_refs = []
    for node in G.nodes():
        if G.nodes[node].get("is_connector", False) or G.nodes[node].get("prefix", "") in ("J",):
            connector_refs.append(node)

    if connector_refs:
        clusters[next_cluster_id] = connector_refs
        for ref in connector_refs:
            assigned[ref] = next_cluster_id
        next_cluster_id += 1

    # Step 3: Crystal → IC mapping
    crystal_to_ic = _find_crystals_and_ics(SG)

    # Step 4: Decoupling cap → IC mapping
    cap_to_ic = _find_decoupling_caps(SG, G)

    # Step 5: Passive → IC mapping
    passive_to_ic = _find_passive_to_ic(SG)

    # Step 6: Build IC-centered clusters
    # Each IC gets its own cluster with associated crystals, caps, and passives
    ic_clusters = {}  # ic_ref -> cluster_id

    for node in G.nodes():
        prefix = G.nodes[node].get("prefix", "")
        if prefix in ("U",) and node not in assigned:
            cluster_id = next_cluster_id
            next_cluster_id += 1
            clusters[cluster_id] = [node]
            assigned[node] = cluster_id
            ic_clusters[node] = cluster_id

    # Attach crystals to their IC's cluster
    for crystal_ref, ic_ref in crystal_to_ic.items():
        if crystal_ref in assigned:
            continue
        if ic_ref in ic_clusters:
            cluster_id = ic_clusters[ic_ref]
            clusters[cluster_id].append(crystal_ref)
            assigned[crystal_ref] = cluster_id

    # Attach decoupling caps to their IC's cluster
    for cap_ref, ic_ref in cap_to_ic.items():
        if cap_ref in assigned:
            continue
        if ic_ref in ic_clusters:
            cluster_id = ic_clusters[ic_ref]
            clusters[cluster_id].append(cap_ref)
            assigned[cap_ref] = cluster_id

    # Attach remaining passives to their IC's cluster
    for passive_ref, ic_ref in passive_to_ic.items():
        if passive_ref in assigned:
            continue
        if ic_ref in ic_clusters:
            cluster_id = ic_clusters[ic_ref]
            if len(clusters[cluster_id]) < MAX_CLUSTER_SIZE:
                clusters[cluster_id].append(passive_ref)
                assigned[passive_ref] = cluster_id

    # Step 7: Merge small IC clusters that are tightly connected
    ic_cluster_ids = list(ic_clusters.values())
    merged = set()

    for i in range(len(ic_cluster_ids)):
        for j in range(i + 1, len(ic_cluster_ids)):
            cid_a, cid_b = ic_cluster_ids[i], ic_cluster_ids[j]
            if cid_a in merged or cid_b in merged:
                continue

            refs_a = clusters[cid_a]
            refs_b = clusters[cid_b]

            # Count cross-cluster signal connections
            cross_edges = 0
            for ra in refs_a:
                for rb in refs_b:
                    if SG.has_edge(ra, rb):
                        cross_edges += SG[ra][rb].get("weight", 1)

            # Merge if strong connection and combined size is reasonable
            combined_size = len(refs_a) + len(refs_b)
            if cross_edges >= 2 and combined_size <= MAX_CLUSTER_SIZE:
                clusters[cid_a].extend(clusters[cid_b])
                for ref in clusters[cid_b]:
                    assigned[ref] = cid_a
                del clusters[cid_b]
                merged.add(cid_b)

    # Step 8: Assign orphan components
    orphans = [node for node in G.nodes() if node not in assigned]
    if orphans:
        # Try to assign each orphan to the cluster of its strongest neighbor
        for orphan in orphans:
            best_cluster = None
            best_weight = 0

            neighbors = list(SG.neighbors(orphan)) if orphan in SG.nodes() else []
            for neighbor in neighbors:
                if neighbor in assigned:
                    cid = assigned[neighbor]
                    weight = SG[orphan][neighbor].get("weight", 1) if SG.has_edge(orphan, neighbor) else 0
                    if weight > best_weight and len(clusters[cid]) < MAX_CLUSTER_SIZE:
                        best_weight = weight
                        best_cluster = cid

            if best_cluster is not None:
                clusters[best_cluster].append(orphan)
                assigned[orphan] = best_cluster
            else:
                # Create a new cluster for this orphan
                clusters[next_cluster_id] = [orphan]
                assigned[orphan] = next_cluster_id
                next_cluster_id += 1

    # Renumber clusters to be contiguous
    renumbered = {}
    for new_id, (old_id, refs) in enumerate(sorted(clusters.items())):
        renumbered[new_id] = refs

    return renumbered


def identify_connector_cluster(G, clusters: dict) -> dict:
    """
    Identify which components are connectors and tag them.
    Returns dict mapping ref -> True if the component is a connector.
    """
    connectors = {}
    for node in G.nodes():
        if G.nodes[node].get("is_connector", False):
            connectors[node] = True
    return connectors


def get_cluster_for_ref(clusters: dict, ref: str) -> int:
    """Return cluster ID for a given component ref."""
    for cluster_id, refs in clusters.items():
        if ref in refs:
            return cluster_id
    return -1


def print_clusters(clusters: dict, G=None):
    """Pretty-print cluster assignments with PCB context."""
    for cluster_id, refs in sorted(clusters.items()):
        details = []
        has_connector = False
        has_ic = False
        has_crystal = False
        for ref in refs:
            label = ref
            if G and ref in G.nodes():
                fp = G.nodes[ref].get("footprint", "?")
                val = G.nodes[ref].get("value", "")
                prefix = G.nodes[ref].get("prefix", "")
                label = f"{ref}({val})"
                if prefix == "J":
                    has_connector = True
                elif prefix == "U":
                    has_ic = True
                elif prefix == "Y":
                    has_crystal = True
            details.append(label)

        # Determine cluster type label
        if has_connector:
            ctype = "CONNECTORS"
        elif has_ic and has_crystal:
            ctype = "IC+CRYSTAL SUBSYSTEM"
        elif has_ic:
            ctype = "IC SUBSYSTEM"
        else:
            ctype = "PASSIVE GROUP"

        print(f"  Cluster {cluster_id} [{ctype}]: {', '.join(details)}")


def merge_llm_blocks(clusters: dict, blocks: list) -> dict:
    """Fold LLM keep_together blocks into a cluster map.

    blocks: iterable of {"name": str, "refs": [str]} (refs pre-validated to
    exist on the optimization graph). Every cluster holding one of a block's
    refs is merged into a single cluster (lowest cluster id wins), so the
    block places as one unit and survives the dense-cluster split pass when
    re-applied after it. Blocks with fewer than two refs are no-ops.

    Returns a new cluster dict; the input is not modified.
    """
    result = {cid: list(refs) for cid, refs in clusters.items()}
    for block in blocks or []:
        block_refs = {str(ref) for ref in (block.get("refs") or [])}
        if len(block_refs) < 2:
            continue
        owning = sorted(
            cid for cid, cluster_refs in result.items()
            if block_refs & set(cluster_refs)
        )
        if len(owning) < 2:
            continue
        merged = []
        for cid in owning:
            merged.extend(result.pop(cid))
        result[owning[0]] = merged
    return result
