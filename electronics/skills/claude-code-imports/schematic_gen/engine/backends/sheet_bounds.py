"""
Sheet bounds calculator for KiCad schematics.

Provides paper size definitions, circuit bounding box computation,
and centering offset calculation to ensure all schematic elements
fit within the visible sheet area.
"""

import logging
from dataclasses import dataclass
from math import ceil, sqrt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sexpdata
from sexpdata import Symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaperSize:
    """KiCad paper size definition with usable area and margins."""

    name: str
    width: float  # full paper width in mm
    height: float  # full paper height in mm
    usable_x: float  # usable drawing width
    usable_y: float  # usable drawing height
    margin_top: float
    margin_bottom: float
    margin_left: float
    margin_right: float


# Standard KiCad paper sizes with usable areas and margins.
# Bottom margin is larger to accommodate the title block.
PAPER_SIZES: Dict[str, PaperSize] = {
    "A4": PaperSize("A4", 297.0, 210.0, 277.0, 175.0, 10.0, 25.0, 10.0, 10.0),
    "A3": PaperSize("A3", 420.0, 297.0, 400.0, 262.0, 10.0, 25.0, 10.0, 10.0),
    "A": PaperSize("A", 279.4, 215.9, 259.4, 180.9, 10.0, 25.0, 10.0, 10.0),
    "B": PaperSize("B", 431.8, 279.4, 411.8, 244.4, 10.0, 25.0, 10.0, 10.0),
}

# Order for auto-scaling: smallest to largest
_PAPER_SCALE_ORDER = ["A4", "A3", "A", "B"]


def get_paper_size(paper_name: str) -> PaperSize:
    """Look up paper size by name, defaulting to A4."""
    return PAPER_SIZES.get(paper_name, PAPER_SIZES["A4"])


def compute_circuit_bbox(schematic_path: Path) -> Tuple[float, float, float, float]:
    """
    Compute the bounding box of all placed elements in a schematic.

    Scans symbols, wires, labels, junctions, no_connects, and text elements.
    Skips elements at extreme negative coordinates (off-screen seeds/templates).

    Returns:
        (min_x, min_y, max_x, max_y) bounding box in mm.
    """
    with open(schematic_path, "r", encoding="utf-8") as fh:
        content = fh.read()

    data = sexpdata.loads(content)

    coords: List[Tuple[float, float]] = []
    _collect_coords(data, coords)

    if not coords:
        return (0.0, 0.0, 0.0, 0.0)

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return (min(xs), min(ys), max(xs), max(ys))


def _collect_coords(sexp: list, coords: List[Tuple[float, float]]) -> None:
    """Recursively collect all coordinate-bearing positions from schematic elements."""
    if not isinstance(sexp, list) or not sexp:
        return

    tag = sexp[0]

    # --- (symbol (lib_id "...") ...) — placed component instances ---
    if tag == Symbol("symbol") and len(sexp) > 1:
        # Check if this is a placed instance (has lib_id) vs lib_symbols definition
        has_lib_id = any(
            isinstance(item, list) and item and item[0] == Symbol("lib_id")
            for item in sexp[1:]
        )
        if has_lib_id:
            _extract_symbol_coords(sexp, coords)
            return  # Don't recurse further into placed instances

    # --- (wire (pts (xy x y) (xy x y))) ---
    if tag == Symbol("wire"):
        _extract_wire_coords(sexp, coords)
        return

    # --- Labels, junctions, no_connect, text ---
    if tag in (
        Symbol("global_label"),
        Symbol("label"),
        Symbol("hierarchical_label"),
        Symbol("net_tie"),
        Symbol("junction"),
        Symbol("no_connect"),
        Symbol("text"),
    ):
        _extract_at_coords(sexp, coords)
        return

    # Recurse into sub-lists
    for item in sexp:
        if isinstance(item, list):
            _collect_coords(item, coords)


def _extract_symbol_coords(sexp: list, coords: List[Tuple[float, float]]) -> None:
    """Extract coordinates from a placed symbol instance."""
    for item in sexp[1:]:
        if not isinstance(item, list) or not item:
            continue

        # Main (at x y [rotation]) — first one is the symbol position
        if item[0] == Symbol("at") and len(item) >= 3:
            x, y = float(item[1]), float(item[2])
            if x > -100 and y > -100:
                coords.append((x, y))
            break  # Only take the first (at ...) for the symbol itself

    # Also collect property positions (Reference, Value, Footprint)
    for item in sexp[1:]:
        if isinstance(item, list) and item and item[0] == Symbol("property"):
            _extract_at_coords(item, coords)


def _extract_wire_coords(sexp: list, coords: List[Tuple[float, float]]) -> None:
    """Extract coordinates from a wire element."""
    for item in sexp[1:]:
        if isinstance(item, list) and item and item[0] == Symbol("pts"):
            for pt in item[1:]:
                if isinstance(pt, list) and pt and pt[0] == Symbol("xy") and len(pt) >= 3:
                    coords.append((float(pt[1]), float(pt[2])))


def _extract_at_coords(sexp: list, coords: List[Tuple[float, float]]) -> None:
    """Extract (at x y ...) coordinates from any element."""
    for item in sexp[1:] if isinstance(sexp, list) else []:
        if isinstance(item, list) and item and item[0] == Symbol("at") and len(item) >= 3:
            x, y = float(item[1]), float(item[2])
            if x > -100 and y > -100:
                coords.append((x, y))
            break  # Only first (at ...) matters


def compute_centering_offset(
    bbox: Tuple[float, float, float, float], paper: PaperSize
) -> Tuple[float, float]:
    """
    Compute the (dx, dy) translation needed to center the circuit on the sheet.

    The offset is clamped so that the circuit's top-left corner never goes
    below the sheet margins (no negative coordinates or out-of-bounds placement).

    Args:
        bbox: (min_x, min_y, max_x, max_y) circuit bounding box
        paper: PaperSize definition

    Returns:
        (dx, dy) offset to apply to all elements
    """
    min_x, min_y, max_x, max_y = bbox

    circuit_center_x = (min_x + max_x) / 2
    circuit_center_y = (min_y + max_y) / 2

    usable_center_x = paper.margin_left + paper.usable_x / 2
    usable_center_y = paper.margin_top + paper.usable_y / 2

    dx = usable_center_x - circuit_center_x
    dy = usable_center_y - circuit_center_y

    # Clamp: ensure the circuit starts at or after the margins
    # (no negative coordinates)
    dx = max(dx, paper.margin_left - min_x)
    dy = max(dy, paper.margin_top - min_y)

    return (dx, dy)


def fits_on_paper(
    bbox: Tuple[float, float, float, float], paper: PaperSize
) -> bool:
    """Check whether the circuit bounding box fits within the paper's usable area."""
    min_x, min_y, max_x, max_y = bbox
    circuit_w = max_x - min_x
    circuit_h = max_y - min_y
    return circuit_w <= paper.usable_x and circuit_h <= paper.usable_y


def select_paper_size(
    bbox: Tuple[float, float, float, float], preferred: str = "A4"
) -> str:
    """
    Select the smallest paper size that fits the circuit.

    Tries the preferred size first, then scales up through A4→A3→A→B.

    Returns:
        Paper size name string
    """
    # Try preferred first
    if preferred in PAPER_SIZES and fits_on_paper(bbox, PAPER_SIZES[preferred]):
        return preferred

    # Try all sizes in order
    for name in _PAPER_SCALE_ORDER:
        if fits_on_paper(bbox, PAPER_SIZES[name]):
            logger.info(f"Auto-scaled paper from {preferred} to {name}")
            return name

    # Nothing fits — use the largest
    logger.warning(
        f"Circuit too large for any standard paper size, using B"
    )
    return "B"
