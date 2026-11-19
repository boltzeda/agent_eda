#!/usr/bin/env python3
"""
Helpers for staged board resizing between placement attempt batches.
"""

import math


def round_up_dim(value: float, quantum: float = 0.5) -> float:
    """Round a board dimension upward to a friendly grid."""
    quantum = max(float(quantum), 1e-6)
    return math.ceil(float(value) / quantum) * quantum


def _next_dimension_stage(current: float, target: float,
                          quantum: float = 0.5,
                          min_step_mm: float = 2.0,
                          max_step_mm: float = 6.0,
                          step_fraction: float = 0.25) -> float:
    """Grow one dimension toward its target without jumping directly there."""
    current = float(current)
    target = float(target)
    if target <= current + 1e-6:
        return current

    delta = target - current
    step = max(float(min_step_mm), delta * float(step_fraction))
    step = min(step, float(max_step_mm), delta)
    staged = min(current + step, target)
    return min(round_up_dim(staged, quantum=quantum), round_up_dim(target, quantum=quantum))


def next_staged_board_resize(current_w: float, current_h: float,
                             target_w: float, target_h: float,
                             quantum: float = 0.5,
                             min_step_mm: float = 2.0,
                             max_step_mm: float = 6.0,
                             step_fraction: float = 0.25) -> tuple[float, float, list[str]]:
    """
    Move the board size toward the target in small stages.
    Width grows first. Height only grows once width has reached target.
    """
    current_w = float(current_w)
    current_h = float(current_h)
    target_w = float(target_w)
    target_h = float(target_h)
    reasons = []

    if target_w > current_w + 1e-6:
        next_w = _next_dimension_stage(
            current_w,
            target_w,
            quantum=quantum,
            min_step_mm=min_step_mm,
            max_step_mm=max_step_mm,
            step_fraction=step_fraction,
        )
        reasons.append(
            f"horizontal staged growth {current_w:.2f} -> {next_w:.2f} mm toward {target_w:.2f} mm"
        )
        return next_w, current_h, reasons

    if target_h > current_h + 1e-6:
        next_h = _next_dimension_stage(
            current_h,
            target_h,
            quantum=quantum,
            min_step_mm=min_step_mm,
            max_step_mm=max_step_mm,
            step_fraction=step_fraction,
        )
        reasons.append(
            f"vertical staged growth {current_h:.2f} -> {next_h:.2f} mm toward {target_h:.2f} mm"
        )
        return current_w, next_h, reasons

    return current_w, current_h, reasons


def build_rejection_resize_target(current_w: float, current_h: float,
                                  cluster_bbox_w: float, cluster_bbox_h: float,
                                  outline_clearance: float,
                                  edge_keepout_margin: float = 0.0,
                                  packing_pressure: float = 0.0,
                                  member_count: int = 0,
                                  quantum: float = 0.5) -> tuple[float, float, list[str]]:
    """
    Build a modest resize target when the same cluster is rejected for a full batch.
    This is intentionally conservative so later staged growth can still happen gradually.
    """
    current_w = float(current_w)
    current_h = float(current_h)
    cluster_bbox_w = float(cluster_bbox_w)
    cluster_bbox_h = float(cluster_bbox_h)
    packing_pressure = max(0.0, min(1.0, float(packing_pressure or 0.0)))
    member_count = max(int(member_count or 0), 0)
    effective_outline = float(outline_clearance) + max(float(edge_keepout_margin or 0.0), 0.0)
    current_inner_w = max(current_w - 2.0 * effective_outline, 1.0)
    current_inner_h = max(current_h - 2.0 * effective_outline, 1.0)

    density_boost = 1.0 + 0.20 * packing_pressure + 0.02 * max(member_count - 3, 0)
    width_slack = max(2.0, 0.06 * current_w, 0.10 * cluster_bbox_w * density_boost)
    height_slack = max(1.5, 0.04 * current_h, 0.08 * cluster_bbox_h * density_boost)

    target_inner_w = max(current_inner_w + width_slack, cluster_bbox_w + 3.0)
    target_inner_h = max(current_inner_h + height_slack, cluster_bbox_h + 2.0)
    target_w = round_up_dim(target_inner_w + 2.0 * effective_outline, quantum=quantum)
    target_h = round_up_dim(target_inner_h + 2.0 * effective_outline, quantum=quantum)

    reasons = [
        (
            "repeated cluster rejection triggered staged horizontal growth "
            f"{current_w:.2f} -> {target_w:.2f} mm"
        )
    ]
    if target_h > current_h + 1e-6:
        reasons.append(
            "repeated cluster rejection prepared vertical reserve "
            f"{current_h:.2f} -> {target_h:.2f} mm"
        )
    return target_w, target_h, reasons


def build_batch_failure_resize_target(current_w: float, current_h: float,
                                      outline_clearance: float,
                                      edge_keepout_margin: float = 0.0,
                                      focus_bbox_w: float = 0.0,
                                      focus_bbox_h: float = 0.0,
                                      focus_member_count: int = 0,
                                      quantum: float = 0.5) -> tuple[float, float, list[str]]:
    """
    Build a low-step growth target after a full failed batch when no exact
    geometric resize target is pending. This keeps the pipeline moving instead
    of stopping after five attempts.
    """
    current_w = float(current_w)
    current_h = float(current_h)
    focus_bbox_w = max(float(focus_bbox_w or 0.0), 0.0)
    focus_bbox_h = max(float(focus_bbox_h or 0.0), 0.0)
    focus_member_count = max(int(focus_member_count or 0), 0)

    effective_outline = float(outline_clearance) + max(float(edge_keepout_margin or 0.0), 0.0)
    current_inner_w = max(current_w - 2.0 * effective_outline, 1.0)
    current_inner_h = max(current_h - 2.0 * effective_outline, 1.0)

    width_slack = max(2.0, 0.05 * current_w, 0.08 * focus_bbox_w)
    height_slack = max(1.0, 0.03 * current_h, 0.06 * focus_bbox_h)

    if focus_member_count > 0:
        width_slack += min(2.0, 0.25 * focus_member_count)
        height_slack += min(1.5, 0.15 * focus_member_count)

    target_inner_w = max(current_inner_w + width_slack, focus_bbox_w + 1.5 if focus_bbox_w > 0.0 else current_inner_w + width_slack)
    target_inner_h = max(current_inner_h + height_slack, focus_bbox_h + 1.0 if focus_bbox_h > 0.0 else current_inner_h + height_slack)
    target_w = round_up_dim(target_inner_w + 2.0 * effective_outline, quantum=quantum)
    target_h = round_up_dim(target_inner_h + 2.0 * effective_outline, quantum=quantum)

    reasons = [
        (
            "incomplete placement batch triggered low-step fallback growth "
            f"{current_w:.2f} -> {target_w:.2f} mm"
        )
    ]
    if target_h > current_h + 1e-6:
        reasons.append(
            "incomplete placement batch prepared low-step vertical reserve "
            f"{current_h:.2f} -> {target_h:.2f} mm"
        )
    return target_w, target_h, reasons
