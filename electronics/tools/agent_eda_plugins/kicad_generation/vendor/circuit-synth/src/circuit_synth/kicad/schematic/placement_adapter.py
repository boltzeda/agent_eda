"""Adapter from loaded circuit/KiCad symbols to the pure placement pipeline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Sequence

from ...core.placement import PLACEMENT_PROPERTY_PREFIX, SchematicPlacementConfig
from ..kicad_symbol_cache import SymbolLibCache
from ..sch_gen.symbol_geometry import SymbolBoundingBoxCalculator
from .professional_placement import (
    LayoutItem,
    LayoutResult,
    ProfessionalSchematicPlacer,
)


@dataclass(frozen=True)
class PlacementPlan:
    """Accepted layout plus mutations that the writer must apply."""

    result: LayoutResult
    component_keys: dict[str, Any]
    child_updates: dict[int, dict[str, Any]]


class SchematicPlacementAdapter:
    """Build a placement problem from one loaded schematic circuit."""

    def __init__(self, circuit, all_subcircuits, config: SchematicPlacementConfig):
        self.circuit = circuit
        self.all_subcircuits = all_subcircuits
        self.config = config

    def create_plan(self, components: Sequence[Any]) -> PlacementPlan:
        items: list[LayoutItem] = []
        component_keys: dict[str, Any] = {}
        reference_keys: dict[str, list[str]] = {}
        source_components = {
            component.reference: component for component in self.circuit.components
        }
        reference_counts: dict[str, int] = {}
        for component in components:
            reference_counts[component.reference] = (
                reference_counts.get(component.reference, 0) + 1
            )

        for component in components:
            unit = int(getattr(component, "unit", 1))
            key = f"{component.reference}_U{unit}"
            component_keys[key] = component
            reference_keys.setdefault(component.reference, []).append(key)

            library_data = SymbolLibCache.get_symbol_data(component.lib_id)
            bbox = (
                SymbolBoundingBoxCalculator.calculate_bounding_box(
                    library_data, include_properties=True
                )
                if library_data
                else (-5.0, -5.0, 5.0, 5.0)
            )
            width, height = self._dimensions_with_connections(
                component, library_data, bbox
            )
            source_component = source_components.get(component.reference, component)
            properties = getattr(source_component, "properties", {}) or {}
            group = str(properties.get(f"{PLACEMENT_PROPERTY_PREFIX}group", "")).strip()
            order = self._optional_int(
                properties.get(f"{PLACEMENT_PROPERTY_PREFIX}order")
            )
            position = self._hint_position(properties)
            fixed = self._as_bool(
                properties.get(f"{PLACEMENT_PROPERTY_PREFIX}fixed", False)
            )

            if unit != 1:
                position = None
                fixed = False
            if not group and reference_counts[component.reference] > 1:
                group = f"component:{component.reference}"

            items.append(
                LayoutItem(
                    key=key,
                    width=width,
                    height=height,
                    group=group,
                    order=order,
                    preferred_position=position,
                    fixed=fixed,
                )
            )

        child_updates = self._add_child_sheet_items(items)
        edges = self._connection_edges(reference_keys)
        result = ProfessionalSchematicPlacer(self.config).place(items, edges)

        for index, updates in child_updates.items():
            key = updates["placement_ref"]
            bbox_x, bbox_y = result.positions[key]
            updates["x"] = bbox_x - updates["bbox_center_offset_x"]
            updates["y"] = bbox_y

        return PlacementPlan(result, component_keys, child_updates)

    def _add_child_sheet_items(
        self, items: list[LayoutItem]
    ) -> dict[int, dict[str, Any]]:
        updates: dict[int, dict[str, Any]] = {}
        for index, child in enumerate(getattr(self.circuit, "child_instances", [])):
            sub_name = child["sub_name"]
            subcircuit = self.all_subcircuits.get(sub_name)
            pin_count = len(subcircuit.nets) if subcircuit else 0
            sheet_height = max(20.32, pin_count * 2.54 + 10.16)
            longest_label = (
                max((len(net.name) for net in subcircuit.nets), default=0)
                if subcircuit
                else 0
            )
            sheet_width = max(25.4, len(sub_name) * 1.5 + 10.0)
            # Sheet-pin electrical endpoints must land on KiCad's 50 mil grid.
            sheet_width = math.ceil(sheet_width / 1.27) * 1.27
            sheet_height = math.ceil(sheet_height / 1.27) * 1.27
            label_extension = longest_label * 1.27 + 10.0
            bbox_width = sheet_width + label_extension
            key = f"SHEET_{index}_{sub_name}"
            updates[index] = {
                "sheet_width": sheet_width,
                "sheet_height": sheet_height,
                "bbox_width": bbox_width,
                "bbox_height": sheet_height,
                "bbox_center_offset_x": label_extension / 2.0,
                "label_extension": label_extension,
                "placement_ref": key,
            }
            items.append(
                LayoutItem(
                    key=key,
                    width=bbox_width,
                    height=sheet_height,
                    group=f"hierarchy:{sub_name}",
                )
            )
        return updates

    def _connection_edges(
        self, reference_keys: dict[str, list[str]]
    ) -> list[tuple[str, str]]:
        edges: list[tuple[str, str]] = []
        available_keys = {
            key for component_keys in reference_keys.values() for key in component_keys
        }
        for net in self.circuit.nets:
            if getattr(net, "is_power", False):
                continue
            endpoint_keys = []
            details = getattr(net, "connection_details", [])
            if details:
                for index, detail in enumerate(details):
                    component_ref = detail.component_ref
                    if component_ref not in reference_keys and index < len(
                        net.connections
                    ):
                        component_ref = net.connections[index][0]
                    unit_key = f"{component_ref}_U{detail.pin_unit}"
                    if unit_key in available_keys:
                        endpoint_keys.append(unit_key)
                    elif component_ref in reference_keys:
                        endpoint_keys.append(reference_keys[component_ref][0])
            else:
                endpoint_keys.extend(
                    reference_keys[reference][0]
                    for reference, _pin in net.connections
                    if reference in reference_keys
                )
            endpoint_keys = list(dict.fromkeys(endpoint_keys))
            if len(endpoint_keys) > self.config.max_signal_fanout:
                continue
            edges.extend(combinations(endpoint_keys, 2))
        return edges

    def _dimensions_with_connections(self, component, library_data, bbox):
        """Measure the symbol plus labels and power symbols generated at its pins."""

        component_rotation = float(getattr(component, "rotation", 0.0))
        rotated_bbox = SymbolBoundingBoxCalculator.rotate_bounding_box(
            tuple(float(value) for value in bbox), component_rotation
        )
        min_x, min_y, max_x, max_y = rotated_bbox
        if not library_data or "pins" not in library_data:
            return max_x - min_x, max_y - min_y

        rotation = math.radians(component_rotation)
        for net in self.circuit.nets:
            for reference, pin_identifier in net.connections:
                if reference != component.reference:
                    continue
                pin = self._find_pin(library_data["pins"], pin_identifier)
                if not pin:
                    continue

                anchor_x = float(pin.get("x", 0.0))
                anchor_y = -float(pin.get("y", 0.0))
                pin_x = anchor_x * math.cos(rotation) - anchor_y * math.sin(rotation)
                pin_y = anchor_x * math.sin(rotation) + anchor_y * math.cos(rotation)
                angle = (
                    float(pin.get("orientation", 0.0)) + 180.0 + component_rotation
                ) % 360
                if getattr(net, "is_power", False):
                    power_data = SymbolLibCache.get_symbol_data(net.power_symbol)
                    if power_data:
                        power_bbox = SymbolBoundingBoxCalculator.calculate_bounding_box(
                            power_data, include_properties=True
                        )
                        power_rotation = (angle - 90.0) % 360.0
                        if "GND" in net.power_symbol or "VSS" in net.power_symbol:
                            power_rotation = (power_rotation + 180.0) % 360.0
                        power_bbox = SymbolBoundingBoxCalculator.rotate_bounding_box(
                            power_bbox, power_rotation
                        )
                        min_x = min(min_x, pin_x + power_bbox[0])
                        min_y = min(min_y, pin_y + power_bbox[1])
                        max_x = max(max_x, pin_x + power_bbox[2])
                        max_y = max(max_y, pin_y + power_bbox[3])
                        continue
                    extension = 6.35
                else:
                    extension = max(2.54, len(net.name) * 1.27)
                side_padding = 2.54

                if angle == 0:
                    max_x = max(max_x, pin_x + extension)
                    min_y = min(min_y, pin_y - side_padding)
                    max_y = max(max_y, pin_y + side_padding)
                elif angle == 90:
                    min_y = min(min_y, pin_y - extension)
                    min_x = min(min_x, pin_x - side_padding)
                    max_x = max(max_x, pin_x + side_padding)
                elif angle == 180:
                    min_x = min(min_x, pin_x - extension)
                    min_y = min(min_y, pin_y - side_padding)
                    max_y = max(max_y, pin_y + side_padding)
                elif angle == 270:
                    max_y = max(max_y, pin_y + extension)
                    min_x = min(min_x, pin_x - side_padding)
                    max_x = max(max_x, pin_x + side_padding)

        # Centers are symbol anchors, so symmetrize asymmetric text extents.
        return 2 * max(abs(min_x), abs(max_x)), 2 * max(abs(min_y), abs(max_y))

    @staticmethod
    def _find_pin(pins, identifier):
        text = str(identifier)
        for field in ("pin_id", "number", "name"):
            match = next((pin for pin in pins if str(pin.get(field)) == text), None)
            if match:
                return match
        return None

    @staticmethod
    def _optional_int(value):
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid placement order: {value!r}") from error

    @staticmethod
    def _as_bool(value):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _hint_position(properties):
        x = properties.get(f"{PLACEMENT_PROPERTY_PREFIX}x")
        y = properties.get(f"{PLACEMENT_PROPERTY_PREFIX}y")
        if x in (None, "") or y in (None, ""):
            return None
        try:
            return float(x), float(y)
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid placement position: ({x!r}, {y!r})") from error
