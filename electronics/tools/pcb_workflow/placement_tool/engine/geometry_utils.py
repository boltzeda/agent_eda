#!/usr/bin/env python3
"""
Shared geometry helpers for placement, collision, and cost evaluation.
"""

DEFAULT_SIZE = (3.0, 3.0)


def coerce_size(size, default=DEFAULT_SIZE):
    """Return a numeric (width, height) pair from tuple/list/dict inputs."""
    if isinstance(size, dict):
        return float(size.get("width", default[0])), float(size.get("height", default[1]))
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        return float(size[0]), float(size[1])
    return float(default[0]), float(default[1])


def extract_pose(value):
    """Normalize placement values to (x, y, rotation)."""
    if isinstance(value, dict):
        return (
            float(value.get("x", 0.0)),
            float(value.get("y", 0.0)),
            float(value.get("rotation", 0.0)),
        )
    if isinstance(value, (list, tuple)):
        if len(value) >= 3:
            return float(value[0]), float(value[1]), float(value[2])
        if len(value) >= 2:
            return float(value[0]), float(value[1]), 0.0
    return 0.0, 0.0, 0.0


def normalize_side(value):
    """Normalize a side value to "top" or "bottom" (anything else -> "top")."""
    side = str(value or "top").strip().lower()
    return "bottom" if side == "bottom" else "top"


def extract_pose_with_side(value):
    """Normalize placement values to (x, y, rotation, side).

    Side-aware cost paths use this; legacy 3-tuple consumers keep
    extract_pose so their contract is unchanged.
    """
    x, y, rotation = extract_pose(value)
    side = normalize_side(value.get("side")) if isinstance(value, dict) else "top"
    return x, y, rotation, side


def rotated_size(size, rotation=0.0):
    """Return footprint size after applying 90/270 degree swaps."""
    w, h = coerce_size(size)
    if int(round(rotation)) % 180 != 0:
        return h, w
    return w, h


def bbox_from_center(x, y, width, height, clearance=0.0):
    """Axis-aligned bounding box expressed as (left, bottom, right, top)."""
    pad = max(float(clearance or 0.0), 0.0)
    half_w = float(width) / 2.0 + pad
    half_h = float(height) / 2.0 + pad
    return (float(x) - half_w, float(y) - half_h, float(x) + half_w, float(y) + half_h)


def component_bbox(x, y, size, rotation=0.0, clearance=0.0):
    """Bounding box for a component centered at (x, y)."""
    w, h = rotated_size(size, rotation=rotation)
    return bbox_from_center(x, y, w, h, clearance=clearance)


def inflate_bbox(box, pad_x, pad_y=None):
    """Expand a box by pad_x/pad_y in each direction."""
    if pad_y is None:
        pad_y = pad_x
    left, bottom, right, top = box
    return (
        left - float(pad_x),
        bottom - float(pad_y),
        right + float(pad_x),
        top + float(pad_y),
    )


def translate_bbox(box, dx, dy):
    """Move a box by (dx, dy)."""
    left, bottom, right, top = box
    return (
        left + float(dx),
        bottom + float(dy),
        right + float(dx),
        top + float(dy),
    )


def clip_bbox(box, bounds):
    """Clip a box to bounds and return None when there is no overlap."""
    left, bottom, right, top = box
    b_left, b_bottom, b_right, b_top = bounds
    clipped = (
        max(left, b_left),
        max(bottom, b_bottom),
        min(right, b_right),
        min(top, b_top),
    )
    if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
        return None
    return clipped


def bbox_overlap(box_a, box_b):
    """Return overlap extents (x, y) between two boxes."""
    left_a, bottom_a, right_a, top_a = box_a
    left_b, bottom_b, right_b, top_b = box_b
    overlap_x = min(right_a, right_b) - max(left_a, left_b)
    overlap_y = min(top_a, top_b) - max(bottom_a, bottom_b)
    return max(0.0, overlap_x), max(0.0, overlap_y)


def bbox_center(box):
    """Return center point of a box."""
    left, bottom, right, top = box
    return (left + right) / 2.0, (bottom + top) / 2.0


def bbox_area(box):
    """Return the non-negative area of a box."""
    left, bottom, right, top = box
    return max(0.0, right - left) * max(0.0, top - bottom)


def union_bboxes(boxes):
    """Return the union box for a sequence of boxes."""
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    bottom = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    top = max(box[3] for box in boxes)
    return (left, bottom, right, top)


def bbox_distance(box_a, box_b):
    """Return edge-to-edge distance between two axis-aligned boxes."""
    dx = max(box_a[0] - box_b[2], box_b[0] - box_a[2], 0.0)
    dy = max(box_a[1] - box_b[3], box_b[1] - box_a[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def keepout_bbox(keepout):
    """Normalize keepout dictionaries to (left, bottom, right, top)."""
    if not keepout:
        return None
    if all(key in keepout for key in ("x1", "y1", "x2", "y2")):
        x1 = float(keepout.get("x1", 0.0))
        y1 = float(keepout.get("y1", 0.0))
        x2 = float(keepout.get("x2", 0.0))
        y2 = float(keepout.get("y2", 0.0))
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if all(key in keepout for key in ("x", "y", "w", "h")):
        x = float(keepout.get("x", 0.0))
        y = float(keepout.get("y", 0.0))
        w = float(keepout.get("w", 0.0))
        h = float(keepout.get("h", 0.0))
        return bbox_from_center(x, y, w, h)
    return None


def hole_radius(hole):
    """Read hole radius from either radius or diameter-based data."""
    if "radius" in hole:
        return float(hole.get("radius", 0.0))
    return float(hole.get("diameter", 0.0)) / 2.0


def hole_bbox(hole, clearance=0.0):
    """Conservative square keepout for a round mounting hole."""
    x = float(hole.get("x", 0.0))
    y = float(hole.get("y", 0.0))
    r = hole_radius(hole) + max(float(clearance or 0.0), 0.0)
    return (x - r, y - r, x + r, y + r)
