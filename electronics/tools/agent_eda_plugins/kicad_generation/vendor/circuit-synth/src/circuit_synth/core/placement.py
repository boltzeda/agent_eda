"""Public configuration and component hints for schematic placement."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping

PLACEMENT_PROPERTY_PREFIX = "_circuit_synth_placement_"


@dataclass(frozen=True)
class SchematicPlacementConfig:
    """Controls automatic placement of newly generated schematics.

    The defaults favor readable, deterministic schematics.  Existing projects still
    use the synchronizer and preserve positions edited in KiCad.
    """

    algorithm: str = "professional"
    min_spacing: float = 5.08
    group_spacing: float = 12.7
    grid_size: float = 2.54
    target_utilization: float = 0.65
    max_retries: int = 4
    expansion_factor: float = 1.25
    max_sheet_size: str = "A1"
    quality_threshold: float = 85.0
    max_signal_fanout: int = 12
    route_wires: bool = True
    wire_grid_size: float = 1.27
    wire_clearance: float = 1.27
    wire_bend_penalty: float = 5.0
    wire_crossing_penalty: float = 25.0

    def __post_init__(self) -> None:
        numeric_values = {
            "min_spacing": self.min_spacing,
            "group_spacing": self.group_spacing,
            "grid_size": self.grid_size,
            "target_utilization": self.target_utilization,
            "expansion_factor": self.expansion_factor,
            "quality_threshold": self.quality_threshold,
            "wire_grid_size": self.wire_grid_size,
            "wire_clearance": self.wire_clearance,
            "wire_bend_penalty": self.wire_bend_penalty,
            "wire_crossing_penalty": self.wire_crossing_penalty,
        }
        for name, value in numeric_values.items():
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.algorithm not in {"professional", "text_flow"}:
            raise ValueError("algorithm must be 'professional' or 'text_flow'")
        if self.min_spacing <= 0 or self.group_spacing < self.min_spacing:
            raise ValueError(
                "group_spacing must be greater than or equal to min_spacing"
            )
        if self.grid_size <= 0:
            raise ValueError("grid_size must be positive")
        if not 0 < self.target_utilization < 1:
            raise ValueError("target_utilization must be between 0 and 1")
        if self.max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        if self.expansion_factor < 1:
            raise ValueError("expansion_factor must be at least 1")
        if self.max_sheet_size not in {"A4", "A3", "A2", "A1"}:
            raise ValueError("max_sheet_size must be one of A4, A3, A2, or A1")
        if not 0 <= self.quality_threshold <= 100:
            raise ValueError("quality_threshold must be between 0 and 100")
        if self.max_signal_fanout < 2:
            raise ValueError("max_signal_fanout must be at least 2")
        if not isinstance(self.route_wires, bool):
            raise ValueError("route_wires must be a boolean")
        if self.wire_grid_size <= 0:
            raise ValueError("wire_grid_size must be positive")
        if self.wire_clearance < 0:
            raise ValueError("wire_clearance cannot be negative")
        if self.wire_bend_penalty < 0 or self.wire_crossing_penalty < 0:
            raise ValueError("wire routing penalties cannot be negative")

    @classmethod
    def from_value(
        cls, value: "SchematicPlacementConfig | Mapping[str, Any] | None"
    ) -> "SchematicPlacementConfig":
        """Normalize a public config value without sharing mutable state."""

        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(**dict(value))
        raise TypeError("schematic_placement must be a config, mapping, or None")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for layout reports."""

        return asdict(self)
