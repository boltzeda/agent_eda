#!/usr/bin/env python3
"""
pcb_auto_placement/graph_utils.py

Builds a PCB-aware NetworkX graph from a parsed KiCad netlist JSON.
Nodes = components (with type classification), Edges = shared signal nets.

PCB-specific features:
  - Power nets (VCC, GND, etc.) are filtered out
  - Large fanout nets (>10) are ignored
  - Edge weights inversely proportional to fanout
  - Component types classified (IC, PASSIVE, CONNECTOR, CRYSTAL, POWER)
  - Net attributes stored (fanout, is_clock, is_power)
"""

import os
import csv
import networkx as nx
from itertools import combinations

# -----------------------------------------------
# Power net names to exclude from graph edges
# -----------------------------------------------
POWER_NET_NAMES = {
    "GND", "AGND", "DGND", "VSS",
    "VCC", "VDD", "AVCC", "AVDD",
    "3V3", "3.3V", "+3V3", "+3.3V",
    "5V", "+5V",
    "12V", "+12V",
    "VBUS", "VBAT",
    "V+", "V-",
}

# Maximum net fanout to include in the graph
MAX_FANOUT = 10

# Component type classification by prefix
IC_PREFIXES = {"U"}
PASSIVE_PREFIXES = {"R", "C", "L"}
CONNECTOR_PREFIXES = {"J", "P", "CN"}
CRYSTAL_PREFIXES = {"Y", "X"}
DIODE_PREFIXES = {"D"}
TRANSISTOR_PREFIXES = {"Q"}
POWER_PREFIXES = {"F", "FB"}


def _classify_component(comp: dict) -> str:
    """
    Classify a component into a PCB type category.

    Returns one of: IC, PASSIVE, CONNECTOR, CRYSTAL, DIODE, TRANSISTOR, POWER, OTHER
    """
    prefix = comp.get("prefix", comp.get("ref", "?")[0])

    if comp.get("is_connector", False) or prefix in CONNECTOR_PREFIXES:
        return "CONNECTOR"
    if comp.get("is_crystal", False) or prefix in CRYSTAL_PREFIXES:
        return "CRYSTAL"
    if prefix in IC_PREFIXES:
        return "IC"
    if prefix in PASSIVE_PREFIXES:
        return "PASSIVE"
    if prefix in DIODE_PREFIXES:
        return "DIODE"
    if prefix in TRANSISTOR_PREFIXES:
        return "TRANSISTOR"
    if prefix in POWER_PREFIXES:
        return "POWER"
    return "OTHER"


def _is_power_net(net_name: str) -> bool:
    """Check if a net name is a power/ground net."""
    # Normalize: strip leading '/' and whitespace, uppercase
    name = net_name.strip().lstrip("/").upper()
    return name in {n.upper() for n in POWER_NET_NAMES}


def _is_clock_net(net_name: str) -> bool:
    """Heuristic: check if a net name looks like a clock signal."""
    name = net_name.upper()
    return any(kw in name for kw in ["CLK", "SCK", "XTAL", "OSC", "CLOCK"])


def classify_connector_type(footprint_name: str) -> str:
    """
    Classify a connector footprint as 'POWER' or 'IO'.
    """
    if not footprint_name:
        return "UNKNOWN"
        
    name = footprint_name.upper()
    if any(kw in name for kw in ["BARREL", "PWR", "POWER"]):
        return "POWER"
    if any(kw in name for kw in ["USB", "JST", "HEADER", "SIL", "DIP", "PIN"]):
        return "IO"
        
    return "UNKNOWN"

def _read_csv(file_path: str) -> list[dict]:
    """Read a CSV file and return a list of dictionaries."""
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def build_netlist_graph(netlist_input: dict | str, footprint_mapping: dict = None) -> nx.Graph:
    """
    Build a PCB-aware connectivity graph from a parsed netlist (JSON dict OR CSV directory path).

    Node attributes:
      - value: component value (e.g. "10K", "ATmega328P")
      - footprint: footprint class name
      - is_connector, is_crystal: bool flags
      - connector_type: 'IO', 'POWER', or 'UNKNOWN'
      - package_type: SMD/THT
      - prefix: reference prefix (U, R, C, etc.)
      - comp_type: classified type (IC, PASSIVE, CONNECTOR, CRYSTAL, etc.)

    Edge attributes:
      - weight: inverse fanout sum (stronger for small nets)
      - nets: list of signal net names
      - net_count: number of shared signal nets

    Filtering:
      - Power nets (VCC, GND, etc.) are excluded
      - Nets with fanout > MAX_FANOUT are excluded
    """
    G = nx.Graph()
    
    if isinstance(netlist_input, str):
        # Assume it's a directory containing CSVs
        csv_dir = netlist_input
        components = _read_csv(os.path.join(csv_dir, "components.csv"))
        nets_list = _read_csv(os.path.join(csv_dir, "nets.csv"))
        net_nodes = _read_csv(os.path.join(csv_dir, "net_nodes.csv"))
        
        # Reconstruct nets with nodes
        nets_map = {n["name"]: {"name": n["name"], "fanout": int(n["fanout"]), "nodes": []} for n in nets_list}
        for node in net_nodes:
            if node["net"] in nets_map:
                nets_map[node["net"]]["nodes"].append({"ref": node["ref"], "pin": node["pin"]})
        nets = list(nets_map.values())
    else:
        components = netlist_input.get("components", [])
        nets = netlist_input.get("nets", [])

    # --- Add nodes with full attributes ---
    for comp in components:
        ref = comp["ref"]
        fp = footprint_mapping.get(ref, "Unknown") if footprint_mapping else "Unknown"
        
        # Check if fp is a dict (enriched metadata) and extract the class name
        if isinstance(fp, dict):
            fp_name = fp.get("class", "Unknown")
        else:
            fp_name = fp
            
        comp_type = _classify_component(comp)
        
        is_conn = comp.get("is_connector", False) or (comp_type == "CONNECTOR")
        conn_type = classify_connector_type(fp_name) if is_conn else "UNKNOWN"

        G.add_node(ref,
                   value=comp.get("value", ""),
                   footprint=fp,
                   is_connector=is_conn,
                   connector_type=conn_type,
                   is_crystal=comp.get("is_crystal", False),
                   package_type=comp.get("package_type", "SMD"),
                   prefix=comp.get("prefix", ref[0]),
                   comp_type=comp_type,
                   num_pins=comp.get("num_pins", 0))

    # --- Add edges from signal nets ---
    for net in nets:
        net_name = net.get("name", "")
        nodes = net.get("nodes", [])
        refs = [n["ref"] for n in nodes if G.has_node(n["ref"])]
        fanout = len(refs)

        # Improvement 1: Skip power nets
        if _is_power_net(net_name):
            continue

        # Improvement 2: Skip very large fanout nets
        if fanout > MAX_FANOUT:
            continue

        # Skip single-pin nets (no connections)
        if fanout < 2:
            continue

        # Net attributes
        is_clock = _is_clock_net(net_name)

        # Improvement 3: Weight inversely proportional to fanout
        # Small nets (fanout=2) get weight 0.5, large nets get less
        edge_weight = 1.0 / fanout

        # Clock nets get extra weight (critical for placement)
        if is_clock:
            edge_weight *= 2.0

        # Improvement 6: Use itertools.combinations for clean pair generation
        for ref_a, ref_b in combinations(refs, 2):
            if G.has_edge(ref_a, ref_b):
                G[ref_a][ref_b]["weight"] += edge_weight
                G[ref_a][ref_b]["nets"].append(net_name)
                G[ref_a][ref_b]["net_count"] += 1
            else:
                G.add_edge(ref_a, ref_b,
                           weight=edge_weight,
                           nets=[net_name],
                           net_count=1)

    return G


def get_component_connections(G: nx.Graph, ref: str) -> list:
    """Return list of component refs connected to the given ref."""
    if ref not in G:
        return []
    return list(G.neighbors(ref))


def get_connectivity_matrix(G: nx.Graph) -> dict:
    """Return adjacency dict: {ref: {neighbor: weight}}."""
    return {node: dict(G[node]) for node in G.nodes()}


def print_graph_summary(G: nx.Graph):
    """Print a summary of the graph for debugging."""
    print(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # Count by type
    type_counts = {}
    for node in G.nodes():
        ctype = G.nodes[node].get("comp_type", "OTHER")
        type_counts[ctype] = type_counts.get(ctype, 0) + 1
    for ctype, count in sorted(type_counts.items()):
        print(f"    {ctype}: {count}")

    # Top connected components
    degrees = sorted(G.degree(), key=lambda x: x[1], reverse=True)
    print(f"  Top connected:")
    for ref, deg in degrees[:5]:
        ctype = G.nodes[ref].get("comp_type", "?")
        print(f"    {ref} ({ctype}): {deg} connections")
