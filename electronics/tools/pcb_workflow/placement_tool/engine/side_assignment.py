#!/usr/bin/env python3
"""
pcb_auto_placement/side_assignment.py

Post-GA side assignment: decides which components mount on the board's
bottom side. Runs after the GA/force-directed passes have fixed x/y/rotation,
so this pass only flips sides — it never moves geometry.

Policy (mirrors the codegen's bottom-side support):
  - SMD parts may move to the bottom when it lowers the side-aware cost.
  - Through-hole parts and connectors stay on top: THT pins pass through the
    board (occupying both sides), and the KiCad bottom-side mirroring in
    codegen_kicad handles SMD pads only.
"""

from cost_functions import compute_total_cost

# Package classes that identify through-hole mountings.
THT_PACKAGE_CLASSES = {"DIP", "SIL", "SIL_2mm"}

# KiCad footprint lib_id fragments that indicate a through-hole part.
# Matched case-insensitively against "<library>:<footprint>".
THT_FOOTPRINT_PATTERNS = (
    "Resistor_THT", "Capacitor_THT", "Inductor_THT", "Diode_THT", "LED_THT",
    "Transistor_THT", "Package_DIP", "Package_THT",
    "Connector", "TerminalBlock", "PinHeader", "PinSocket",
    "Crystal", "Button_Switch_THT", "Relay_THT", "Fuse_THT",
    "MountingHole",
)

# THT power-package families spelled out so the SMD variants stay SMD:
# "Package_TO" alone would also match "Package_TO_SOT_SMD" (SOT-223 & co).
THT_POWER_PACKAGE_PATTERNS = (
    "Package_TO_SOT_THT",   # KiCad 6+: TO-92/TO-126/TO-220... through-hole
    "TO_SOT_Packages_THT",  # KiCad 5 library name
    "Package_TO-",          # legacy bare TO-92/TO-220 style names
)


def _lib_id_is_tht(lib_id: str) -> bool:
    """True when a KiCad "<library>:<footprint>" id is through-hole."""
    lid = str(lib_id or "").lower()
    if any(pattern.lower() in lid for pattern in THT_POWER_PACKAGE_PATTERNS):
        return True
    if "_smd" in lid:
        return False
    return any(pattern.lower() in lid for pattern in THT_FOOTPRINT_PATTERNS)


def infer_package_types(footprint_mapping: dict) -> dict:
    """Classify each ref as "SMD" or "THT" from the footprint classifier output.

    Unknown footprints default to THT (conservative: they stay on top).
    """
    package_types = {}
    for ref, entry in (footprint_mapping or {}).items():
        if not isinstance(entry, dict):
            package_types[ref] = "THT"
            continue
        classes = {
            str(entry.get("class") or ""),
            str(entry.get("fallback_class") or ""),
        }
        lib_id = str(entry.get("kicad_footprint") or "")
        if classes & THT_PACKAGE_CLASSES or _lib_id_is_tht(lib_id):
            package_types[ref] = "THT"
        else:
            package_types[ref] = "SMD"
    return package_types


def collect_tht_refs(G, footprint_mapping: dict, fixed_refs=None) -> set:
    """Refs that must stay on top: connectors, fixed positions, and THT parts."""
    tht_refs = set(fixed_refs or [])
    if G is not None:
        for ref in G.nodes():
            if G.nodes[ref].get("is_connector", False):
                tht_refs.add(ref)
    for ref, package in infer_package_types(footprint_mapping).items():
        if package == "THT":
            tht_refs.add(ref)
    return tht_refs


def _attach_rotations(placement: dict, rotations: dict) -> dict:
    """Local copy of main_pipeline.attach_rotations semantics (no import cycle)."""
    result = {}
    for ref, coord in placement.items():
        if len(coord) >= 3:
            result[ref] = (coord[0], coord[1], coord[2])
        else:
            result[ref] = (coord[0], coord[1], (rotations or {}).get(ref, 0.0))
    return result


def assign_sides(placement: dict, G, sizes: dict, rotations: dict,
                 board_w: float, board_h: float,
                 holes: list, keepouts: list,
                 footprint_mapping: dict,
                 outline_clearance: float = 0.0,
                 component_clearance: float = 0.0,
                 hole_clearance: float = None,
                 edge_keepout_margin: float = 0.0,
                 fixed_refs=None,
                 bottom_bias: float = 20.0,
                 max_sweeps: int = 2,
                 initial_sides=None,
                 locked_refs=None,
                 forced_sides=None,
                 per_ref_bottom_bias=None,
                 log=print):
    """Greedy smallest-first side assignment over the GA placement.

    Deterministic: candidates are sorted by footprint area then ref name,
    accepts are epsilon-guarded, and no RNG is used. x/y/rotation are never
    modified — the placement JSON keeps the GA geometry, only "side" changes.

    initial_sides seeds the starting assignment (e.g. bottom-overflow sides
    from cluster seeding); each candidate is then toggled in both directions,
    so a seeded bottom part flips back to top when the GA made room for it.

    LLM side hints: locked_refs (lock_top ∪ lock_bottom) leave the candidate
    set — the greedy pass never toggles them. forced_sides overrides
    initial_sides for those refs, so a hint wins over seeded overflow.
    per_ref_bottom_bias scales the bias per prefer_top ref.

    Returns (sides, stats) where sides maps every placement ref to
    "top"/"bottom" and stats carries the flip list and cost before/after.
    """
    pose = _attach_rotations(placement, rotations or {})
    tht_refs = collect_tht_refs(G, footprint_mapping, fixed_refs=fixed_refs)
    locked_refs = set(locked_refs or [])
    candidates = sorted(
        (ref for ref in pose if ref not in tht_refs and ref not in locked_refs),
        key=lambda ref: (
            float(sizes.get(ref, (3.0, 3.0))[0]) * float(sizes.get(ref, (3.0, 3.0))[1]),
            str(ref),
        ),
    )

    initial = dict(initial_sides or {})
    for ref, side in (forced_sides or {}).items():
        if ref in pose:
            initial[ref] = side
    sides = {
        ref: ("bottom" if initial.get(ref) == "bottom" and ref not in tht_refs else "top")
        for ref in pose
    }
    initial_bottom = sorted(ref for ref, side in sides.items() if side == "bottom")

    def cost():
        return compute_total_cost(
            pose, G, sizes, board_w, board_h,
            holes=holes or [], keepouts=keepouts or [],
            outline_clearance=outline_clearance,
            component_clearance=component_clearance,
            hole_clearance=hole_clearance,
            edge_keepout_margin=edge_keepout_margin,
            sides=sides, tht_refs=tht_refs, bottom_bias=bottom_bias,
            bottom_bias_weights=per_ref_bottom_bias,
        )

    cost_before = cost()
    current = cost_before
    flipped = []

    for _sweep in range(max(1, int(max_sweeps))):
        sweep_flips = 0
        for ref in candidates:
            trial_side = "bottom" if sides[ref] == "top" else "top"
            sides[ref] = trial_side
            trial = cost()
            if trial < current - 1e-9:
                current = trial
                sweep_flips += 1
                if ref not in flipped:
                    flipped.append(ref)
                log(f"  Moved {ref} to {trial_side} (cost {trial:.2f})")
            else:
                sides[ref] = "top" if trial_side == "bottom" else "bottom"
        if sweep_flips == 0:
            break

    return sides, {
        "flipped": flipped,
        "cost_before": cost_before,
        "cost_after": cost(),
        "tht_refs": sorted(tht_refs),
        "candidates": candidates,
        "initial_bottom": initial_bottom,
        "locked_refs": sorted(locked_refs),
    }
