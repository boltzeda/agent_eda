"""Deterministic, constraint-aware placement for generated schematics.

The module contains no KiCad API dependencies.  It accepts measured rectangles and
returns grid-aligned centers, which keeps placement policy independently testable.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from ...core.placement import SchematicPlacementConfig


@dataclass(frozen=True)
class SheetDef:
    """A landscape schematic sheet and its usable drawing area, in millimetres."""

    name: str
    width: float
    height: float
    min_x: float
    min_y: float
    max_x: float
    max_y: float


SHEET_SIZES: tuple[SheetDef, ...] = (
    SheetDef("A4", 297.0, 210.0, 12.7, 12.7, 284.48, 165.10),
    SheetDef("A3", 420.0, 297.0, 12.7, 12.7, 407.0, 277.0),
    SheetDef("A2", 594.0, 420.0, 12.7, 12.7, 581.0, 400.0),
    SheetDef("A1", 841.0, 594.0, 12.7, 12.7, 828.0, 574.0),
)


@dataclass(frozen=True)
class LayoutItem:
    """Measured object to place on a schematic sheet."""

    key: str
    width: float
    height: float
    group: str = ""
    order: int | None = None
    preferred_position: tuple[float, float] | None = None
    fixed: bool = False

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("layout item key cannot be empty")
        if (
            not math.isfinite(self.width)
            or not math.isfinite(self.height)
            or self.width <= 0
            or self.height <= 0
        ):
            raise ValueError(f"layout item {self.key!r} must have positive dimensions")
        if self.fixed and self.preferred_position is None:
            raise ValueError(f"fixed layout item {self.key!r} requires a position")
        if self.preferred_position is not None and not all(
            math.isfinite(value) for value in self.preferred_position
        ):
            raise ValueError(f"layout item {self.key!r} position must be finite")


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle used by the legalizer and quality analyzer."""

    left: float
    top: float
    right: float
    bottom: float

    @classmethod
    def from_center(
        cls, center: tuple[float, float], width: float, height: float
    ) -> "Rect":
        x, y = center
        return cls(x - width / 2, y - height / 2, x + width / 2, y + height / 2)

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return self.width * self.height

    def intersects(self, other: "Rect", clearance: float = 0.0) -> bool:
        return not (
            self.right + clearance <= other.left
            or other.right + clearance <= self.left
            or self.bottom + clearance <= other.top
            or other.bottom + clearance <= self.top
        )

    def overlap_area(self, other: "Rect") -> float:
        width = min(self.right, other.right) - max(self.left, other.left)
        height = min(self.bottom, other.bottom) - max(self.top, other.top)
        return max(0.0, width) * max(0.0, height)

    def inside(self, sheet: SheetDef) -> bool:
        epsilon = 1e-6
        return (
            self.left >= sheet.min_x - epsilon
            and self.top >= sheet.min_y - epsilon
            and self.right <= sheet.max_x + epsilon
            and self.bottom <= sheet.max_y + epsilon
        )


@dataclass(frozen=True)
class LayoutMetrics:
    """Objective measurements used to accept or retry a placement."""

    score: float
    overlap_count: int
    overlap_area: float
    clearance_violations: int
    overflow_count: int
    utilization: float
    estimated_wire_length: float
    item_count: int

    @property
    def legal(self) -> bool:
        return (
            self.overlap_count == 0
            and self.clearance_violations == 0
            and self.overflow_count == 0
        )

    def to_dict(self) -> dict[str, float | int | bool]:
        data = asdict(self)
        data["legal"] = self.legal
        return data


@dataclass(frozen=True)
class LayoutResult:
    """Selected placement and the evidence used to select it."""

    positions: dict[str, tuple[float, float]]
    sheet: SheetDef
    spacing: float
    attempts: int
    metrics: LayoutMetrics
    threshold_met: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "sheet": self.sheet.name,
            "sheet_dimensions": [self.sheet.width, self.sheet.height],
            "spacing": self.spacing,
            "attempts": self.attempts,
            "threshold_met": self.threshold_met,
            "metrics": self.metrics.to_dict(),
            "positions": {
                key: {"x": position[0], "y": position[1]}
                for key, position in sorted(self.positions.items())
            },
        }


class PlacementError(RuntimeError):
    """Raised when legal placement is impossible within configured constraints."""


class LayoutQualityAnalyzer:
    """Scores legal geometry, clearance, density, and connection length."""

    def __init__(self, config: SchematicPlacementConfig):
        self.config = config

    def analyze(
        self,
        items: Sequence[LayoutItem],
        positions: dict[str, tuple[float, float]],
        sheet: SheetDef,
        spacing: float,
        connections: Iterable[tuple[str, str]] = (),
    ) -> LayoutMetrics:
        item_by_key = {item.key: item for item in items}
        rectangles = {
            key: Rect.from_center(positions[key], item.width, item.height)
            for key, item in item_by_key.items()
            if key in positions
        }

        overlap_count = 0
        overlap_area = 0.0
        clearance_violations = 0
        keys = sorted(rectangles)
        for index, key in enumerate(keys):
            first = rectangles[key]
            for other_index in range(index + 1, len(keys)):
                other_key = keys[other_index]
                second = rectangles[other_key]
                first_item = item_by_key[key]
                second_item = item_by_key[other_key]
                required_clearance = (
                    max(spacing, self.config.group_spacing)
                    if first_item.group != second_item.group
                    and (first_item.group or second_item.group)
                    else spacing
                )
                area = first.overlap_area(second)
                if area > 0:
                    overlap_count += 1
                    overlap_area += area
                elif first.intersects(second, clearance=required_clearance):
                    clearance_violations += 1

        overflow_count = sum(not rect.inside(sheet) for rect in rectangles.values())
        usable_area = (sheet.max_x - sheet.min_x) * (sheet.max_y - sheet.min_y)
        item_area = sum(item.width * item.height for item in items)
        utilization = item_area / usable_area if usable_area else 1.0

        unique_edges = {
            tuple(sorted((start, end)))
            for start, end in connections
            if start != end and start in positions and end in positions
        }
        wire_length = sum(
            abs(positions[start][0] - positions[end][0])
            + abs(positions[start][1] - positions[end][1])
            for start, end in unique_edges
        )

        count = max(1, len(items))
        total_area = max(1.0, item_area)
        overlap_penalty = min(
            1.0, overlap_count / max(1.0, count / 10) + overlap_area / total_area
        )
        clearance_penalty = min(1.0, clearance_violations / max(1.0, count))
        overflow_penalty = min(1.0, overflow_count / count)
        density_penalty = max(
            0.0,
            (utilization - self.config.target_utilization)
            / (1.0 - self.config.target_utilization),
        )
        characteristic_size = max(
            self.config.grid_size,
            math.sqrt(item_area / count),
        )
        wire_penalty = (
            min(1.0, wire_length / (len(unique_edges) * characteristic_size * 20.0))
            if unique_edges
            else 0.0
        )

        score = 100.0 * (
            1.0
            - 0.50 * overlap_penalty
            - 0.20 * overflow_penalty
            - 0.15 * clearance_penalty
            - 0.05 * density_penalty
            - 0.10 * wire_penalty
        )
        # A missing placement is always an overflow-equivalent failure.
        missing_count = len(items) - len(rectangles)
        if missing_count:
            overflow_count += missing_count
            score -= 20.0 * missing_count / count

        return LayoutMetrics(
            score=round(max(0.0, score), 2),
            overlap_count=overlap_count,
            overlap_area=round(overlap_area, 3),
            clearance_violations=clearance_violations,
            overflow_count=overflow_count,
            utilization=round(utilization, 4),
            estimated_wire_length=round(wire_length, 3),
            item_count=len(items),
        )


class ProfessionalSchematicPlacer:
    """Adaptive shelf placer followed by deterministic non-overlap legalization."""

    def __init__(self, config: SchematicPlacementConfig | None = None):
        self.config = config or SchematicPlacementConfig()
        self.analyzer = LayoutQualityAnalyzer(self.config)

    def place(
        self,
        items: Sequence[LayoutItem],
        connections: Iterable[tuple[str, str]] = (),
    ) -> LayoutResult:
        if not items:
            sheet = SHEET_SIZES[0]
            metrics = self.analyzer.analyze([], {}, sheet, self.config.min_spacing)
            return LayoutResult(
                {}, sheet, self.config.min_spacing, 0, metrics, threshold_met=True
            )

        self._validate_unique_keys(items)
        edge_list = tuple(connections)
        ordered_items = self._order_items(items, edge_list)
        sheets = self._allowed_sheets()
        base_spacing = self._base_spacing(items)
        best: LayoutResult | None = None
        attempts = 0
        last_error: Exception | None = None

        for sheet in sheets:
            for retry in range(self.config.max_retries):
                spacing = base_spacing * self.config.expansion_factor**retry
                attempts += 1
                try:
                    positions = self._pack_and_legalize(ordered_items, sheet, spacing)
                except PlacementError as error:
                    last_error = error
                    continue

                metrics = self.analyzer.analyze(
                    items, positions, sheet, spacing, edge_list
                )
                threshold_met = metrics.score >= self.config.quality_threshold
                result = LayoutResult(
                    positions,
                    sheet,
                    spacing,
                    attempts,
                    metrics,
                    threshold_met=threshold_met,
                )
                if best is None or result.metrics.score > best.metrics.score:
                    best = result
                density_ok = metrics.utilization <= self.config.target_utilization
                if metrics.legal and density_ok and threshold_met:
                    return result

        if best is not None and best.metrics.legal:
            raise PlacementError(
                f"best legal schematic placement scored {best.metrics.score:.2f}, "
                f"below quality threshold {self.config.quality_threshold:.2f}"
            )
        detail = f": {last_error}" if last_error else ""
        raise PlacementError(
            f"unable to produce a legal schematic placement through "
            f"{self.config.max_sheet_size}{detail}"
        )

    def _allowed_sheets(self) -> tuple[SheetDef, ...]:
        maximum = next(
            index
            for index, sheet in enumerate(SHEET_SIZES)
            if sheet.name == self.config.max_sheet_size
        )
        return SHEET_SIZES[: maximum + 1]

    def _base_spacing(self, items: Sequence[LayoutItem]) -> float:
        """Scale whitespace for large symbols while respecting configured bounds."""

        average_area = sum(item.width * item.height for item in items) / len(items)
        density_spacing = (
            math.sqrt(average_area) * (1.0 - self.config.target_utilization) / 2.0
        )
        return min(
            self.config.group_spacing,
            max(self.config.min_spacing, density_spacing),
        )

    @staticmethod
    def _validate_unique_keys(items: Sequence[LayoutItem]) -> None:
        keys = [item.key for item in items]
        if len(keys) != len(set(keys)):
            raise ValueError("layout item keys must be unique")

    def _order_items(
        self,
        items: Sequence[LayoutItem],
        connections: Sequence[tuple[str, str]],
    ) -> list[LayoutItem]:
        """Keep explicit groups together and connected components near each other."""

        item_by_key = {item.key: item for item in items}
        group_order: list[str] = []
        grouped: dict[str, list[LayoutItem]] = {}
        for item in items:
            group = item.group or "__ungrouped__"
            if group not in grouped:
                grouped[group] = []
                group_order.append(group)
            grouped[group].append(item)

        adjacency: dict[str, set[str]] = {item.key: set() for item in items}
        for first, second in connections:
            if first in item_by_key and second in item_by_key and first != second:
                adjacency[first].add(second)
                adjacency[second].add(first)

        ordered: list[LayoutItem] = []
        for group in group_order:
            members = grouped[group]
            explicit = sorted(
                (item for item in members if item.order is not None),
                key=lambda item: (item.order, item.key),
            )
            remaining = {item.key for item in members if item.order is None}
            traversal: list[str] = []
            while remaining:
                seed = min(
                    remaining,
                    key=lambda key: (-len(adjacency[key] & remaining), key),
                )
                queue = [seed]
                remaining.remove(seed)
                while queue:
                    current = queue.pop(0)
                    traversal.append(current)
                    neighbors = sorted(
                        adjacency[current] & remaining,
                        key=lambda key: (-len(adjacency[key] & remaining), key),
                    )
                    for neighbor in neighbors:
                        remaining.remove(neighbor)
                        queue.append(neighbor)
            ordered.extend(explicit)
            ordered.extend(item_by_key[key] for key in traversal)
        return ordered

    def _pack_and_legalize(
        self, items: Sequence[LayoutItem], sheet: SheetDef, spacing: float
    ) -> dict[str, tuple[float, float]]:
        positions: dict[str, tuple[float, float]] = {}
        rectangles: dict[str, Rect] = {}
        item_by_key = {item.key: item for item in items}

        fixed_items = [item for item in items if item.fixed]
        for item in fixed_items:
            assert item.preferred_position is not None
            center = item.preferred_position
            rectangle = Rect.from_center(center, item.width, item.height)
            if not rectangle.inside(sheet):
                raise PlacementError(f"fixed item {item.key} is outside {sheet.name}")
            blockers = [
                key
                for key, other in rectangles.items()
                if rectangle.intersects(
                    other,
                    clearance=self._pair_clearance(item, item_by_key[key], spacing),
                )
            ]
            if blockers:
                raise PlacementError(
                    f"fixed item {item.key} conflicts with {', '.join(blockers)}"
                )
            positions[item.key] = center
            rectangles[item.key] = rectangle

        for item in (item for item in items if not item.fixed):
            preferred = item.preferred_position
            if preferred is not None:
                preferred = self._snap_position(preferred)
                preferred_rect = Rect.from_center(preferred, item.width, item.height)
                if preferred_rect.inside(sheet) and not self._blockers(
                    item,
                    preferred_rect,
                    rectangles,
                    item_by_key,
                    spacing,
                ):
                    positions[item.key] = preferred
                    rectangles[item.key] = preferred_rect
                    continue

            center, rectangle = self._find_position(
                item,
                sheet,
                spacing,
                rectangles,
                item_by_key,
            )
            positions[item.key] = center
            rectangles[item.key] = rectangle

        return positions

    def _find_position(
        self,
        item: LayoutItem,
        sheet: SheetDef,
        spacing: float,
        rectangles: dict[str, Rect],
        item_by_key: dict[str, LayoutItem],
    ) -> tuple[tuple[float, float], Rect]:
        """Choose the first legal bottom-left candidate around all obstacles."""

        left_candidates = {sheet.min_x}
        top_candidates = {sheet.min_y}
        for key, rectangle in rectangles.items():
            clearance = self._pair_clearance(item, item_by_key[key], spacing)
            left_candidates.add(rectangle.right + clearance)
            top_candidates.add(rectangle.bottom + clearance)

        for top in sorted(top_candidates):
            for left in sorted(left_candidates):
                center = (
                    self._snap_up(left + item.width / 2),
                    self._snap_up(top + item.height / 2),
                )
                rectangle = Rect.from_center(center, item.width, item.height)
                if not rectangle.inside(sheet):
                    continue
                if not self._blockers(
                    item, rectangle, rectangles, item_by_key, spacing
                ):
                    return center, rectangle

        raise PlacementError(f"item {item.key} does not fit on {sheet.name}")

    def _blockers(
        self,
        item: LayoutItem,
        rectangle: Rect,
        rectangles: dict[str, Rect],
        item_by_key: dict[str, LayoutItem],
        spacing: float,
    ) -> list[str]:
        return [
            key
            for key, other in rectangles.items()
            if rectangle.intersects(
                other,
                clearance=self._pair_clearance(item, item_by_key[key], spacing),
            )
        ]

    def _pair_clearance(
        self, first: LayoutItem, second: LayoutItem, spacing: float
    ) -> float:
        if first.group != second.group and (first.group or second.group):
            return max(spacing, self.config.group_spacing)
        return spacing

    def _snap_up(self, value: float) -> float:
        return math.ceil((value - 1e-9) / self.config.grid_size) * self.config.grid_size

    def _snap_position(self, position: tuple[float, float]) -> tuple[float, float]:
        x, y = position
        return (
            round(x / self.config.grid_size) * self.config.grid_size,
            round(y / self.config.grid_size) * self.config.grid_size,
        )
