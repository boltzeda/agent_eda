"""Circuit definition validation and auto-layout."""

import logging
from math import ceil, sqrt
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_GRID = 12.7  # Minimum auto-layout spacing in mm


def validate_circuit(
    circuit: Dict[str, Any], paper_size: str = "A4"
) -> Dict[str, Any]:
    """Validate and normalize a circuit definition.

    Ensures required fields exist, normalizes types, and assigns
    auto-layout positions to components missing x/y coordinates.
    The auto-layout adapts grid spacing to fit within the paper's
    usable area.
    """
    components = circuit.get("components", [])
    nets = circuit.get("nets", [])

    if not components:
        raise ValueError("Circuit must have at least one component")

    validated = []
    for i, comp in enumerate(components):
        if "ref" not in comp:
            raise ValueError(f"Component at index {i} missing 'ref'")

        entry = {
            "ref": comp["ref"],
            "type": comp.get("type", "R"),
            "value": comp.get("value", ""),
            "footprint": comp.get("footprint", ""),
            "x": comp.get("x"),
            "y": comp.get("y"),
            "rotation": comp.get("rotation", 0),
        }
        validated.append(entry)

    # Auto-layout for components without positions
    _auto_layout(validated, paper_size=paper_size)

    circuit_out = {"components": validated, "nets": nets}
    return circuit_out


def _auto_layout(
    components: List[Dict[str, Any]], paper_size: str = "A4"
) -> None:
    """Assign grid positions to components missing x/y.

    Adapts grid spacing to the paper's usable area so components
    are evenly distributed and centered. Falls back to the minimum
    12.7mm grid when the paper is large enough.
    """
    needs_layout = [c for c in components if c["x"] is None or c["y"] is None]
    if not needs_layout:
        return

    n = len(needs_layout)
    cols = max(1, int(sqrt(n)) + 1)
    rows = ceil(n / cols)

    # Resolve paper dimensions
    from engine.backends.sheet_bounds import get_paper_size

    paper = get_paper_size(paper_size)

    # Compute adaptive grid spacing that fills the usable area
    # Leave 10mm internal padding on each side within the usable region
    inner_w = paper.usable_x - 20.0
    inner_h = paper.usable_y - 20.0

    grid_x = inner_w / max(cols, 1)
    grid_y = inner_h / max(rows, 1)
    grid = max(MIN_GRID, min(grid_x, grid_y))

    # Recalculate cols/rows with the actual grid
    cols = max(1, int(inner_w / grid))
    rows = ceil(n / cols)

    # Center the grid within the usable area
    total_w = (cols - 1) * grid
    total_h = (rows - 1) * grid
    start_x = paper.margin_left + 10.0 + (inner_w - total_w) / 2
    start_y = paper.margin_top + 10.0 + (inner_h - total_h) / 2

    # Snap start to 2.54mm grid
    start_x = round(start_x / 2.54) * 2.54
    start_y = round(start_y / 2.54) * 2.54

    logger.info(
        f"Auto-layout: {n} components in {cols}x{rows} grid, "
        f"spacing={grid:.1f}mm, start=({start_x:.1f}, {start_y:.1f}) "
        f"on {paper_name}"
    )

    for i, comp in enumerate(needs_layout):
        col = i % cols
        row = i // cols
        if comp["x"] is None:
            comp["x"] = round(start_x + col * grid, 2)
        if comp["y"] is None:
            comp["y"] = round(start_y + row * grid, 2)


def circuit_summary(circuit: Dict[str, Any]) -> str:
    """Return a human-readable summary of the circuit definition."""
    components = circuit.get("components", [])
    nets = circuit.get("nets", [])

    lines = [f"Components: {len(components)}"]
    for c in components:
        lines.append(
            f"  {c['ref']} ({c.get('type', 'R')}): "
            f"value={c.get('value', '')} at ({c.get('x')}, {c.get('y')})"
        )

    lines.append(f"Nets: {len(nets)}")
    for n in nets:
        pins = [f"{p['ref']}.{p['pin']}" for p in n.get("pins", [])]
        lines.append(f"  {n['name']}: {pins}")

    return "\n".join(lines)
