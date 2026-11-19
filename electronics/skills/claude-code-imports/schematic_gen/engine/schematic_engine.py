"""Core schematic generation engine.

Loads a seed template, clones symbols, places components,
routes wires, adds net labels, and writes a valid .kicad_sch file.
"""

import copy
import logging
import math
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sexpdata
from sexpdata import Symbol
from skip import Schematic

from engine.backends.dynamic_symbol_loader import DynamicSymbolLoader
from engine.backends.pin_locator import PinLocator
from engine.backends.wire_manager import WireManager

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
DEFAULT_TEMPLATE = TEMPLATES_DIR / "seed_template.kicad_sch"

TYPE_MAP: Dict[str, Tuple[str, str, str]] = {
    "r": ("Device", "R", "_SEED_R"),
    "resistor": ("Device", "R", "_SEED_R"),
    "c": ("Device", "C", "_SEED_C"),
    "capacitor": ("Device", "C", "_SEED_C"),
    "l": ("Device", "L", "_SEED_L"),
    "inductor": ("Device", "L", "_SEED_L"),
    "crystal": ("Device", "Crystal", "_SEED_Y"),
    "d": ("Device", "D", "_SEED_D"),
    "diode": ("Device", "D", "_SEED_D"),
    "led": ("Device", "LED", "_SEED_LED"),
    "q_npn": ("Device", "Q_NPN", "_SEED_Q_NPN"),
    "transistor_npn": ("Device", "Q_NPN", "_SEED_Q_NPN"),
    "npn": ("Device", "Q_NPN", "_SEED_Q_NPN"),
    "q_nmos": ("Device", "Q_NMOS", "_SEED_Q_NMOS"),
    "transistor_nmos": ("Device", "Q_NMOS", "_SEED_Q_NMOS"),
    "nmos": ("Device", "Q_NMOS", "_SEED_Q_NMOS"),
    "opamp": ("Amplifier_Operational", "LM358", "_SEED_U_OPAMP"),
    "lm358": ("Amplifier_Operational", "LM358", "_SEED_U_OPAMP"),
    "connector_01x02": ("Connector_Generic", "Conn_01x02", "_SEED_J2"),
    "connector_01x04": ("Connector_Generic", "Conn_01x04", "_SEED_J4"),
    "conn_01x02": ("Connector_Generic", "Conn_01x02", "_SEED_J2"),
    "conn_01x04": ("Connector_Generic", "Conn_01x04", "_SEED_J4"),
    "regulator": ("Regulator_Linear", "AMS1117-3.3", "_SEED_U_REG"),
    "ams1117": ("Regulator_Linear", "AMS1117-3.3", "_SEED_U_REG"),
    "switch": ("Switch", "SW_Push", "_SEED_SW"),
    "sw_push": ("Switch", "SW_Push", "_SEED_SW"),
    "button": ("Switch", "SW_Push", "_SEED_SW"),
    "pwr_flag": ("power", "PWR_FLAG", "_SEED_PWR"),
}

FOOTPRINT_MAP: Dict[str, str] = {
    "r": "Resistor_SMD:R_0805_2012Metric",
    "resistor": "Resistor_SMD:R_0805_2012Metric",
    "c": "Capacitor_SMD:C_0805_2012Metric",
    "capacitor": "Capacitor_SMD:C_0805_2012Metric",
    "l": "Inductor_SMD:L_0805_2012Metric",
    "inductor": "Inductor_SMD:L_0805_2012Metric",
    "crystal": "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
    "d": "Diode_SMD:D_0805_2012Metric",
    "diode": "Diode_SMD:D_0805_2012Metric",
    "led": "LED_SMD:LED_0805_2012Metric",
    "q_npn": "Package_TO_SOT_THT:TO-92-2",
    "transistor_npn": "Package_TO_SOT_THT:TO-92-2",
    "npn": "Package_TO_SOT_THT:TO-92-2",
    "q_nmos": "Package_TO_SOT_SMD:SOT-23",
    "transistor_nmos": "Package_TO_SOT_SMD:SOT-23",
    "nmos": "Package_TO_SOT_SMD:SOT-23",
    "opamp": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "lm358": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "connector_01x02": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "connector_01x04": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "conn_01x02": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "conn_01x04": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "regulator": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    "ams1117": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    "switch": "Button_Switch_THT:SW_PUSH_1P1T_6x3.5mm_H4.3_APEM_MJTP1243",
    "sw_push": "Button_Switch_THT:SW_PUSH_1P1T_6x3.5mm_H4.3_APEM_MJTP1243",
    "button": "Button_Switch_THT:SW_PUSH_1P1T_6x3.5mm_H4.3_APEM_MJTP1243",
}

POWER_NETS = {
    "VCC", "VDD", "+5V", "+3V3", "+3.3V", "3V3", "5V",
    "GND", "VSS", "DGND", "AGND",
}

GRID = 2.54
LABEL_OFFSET = 3 * GRID  # 7.62mm - distance from pin to net label

REF_PREFIXES: Dict[str, str] = {
    "r": "R", "resistor": "R", "c": "C", "capacitor": "C",
    "l": "L", "inductor": "L", "crystal": "Y", "d": "D", "diode": "D",
    "led": "D", "q_npn": "Q", "npn": "Q", "q_nmos": "Q", "nmos": "Q",
    "opamp": "U", "lm358": "U", "connector_01x02": "J", "connector_01x04": "J",
    "conn_01x02": "J", "conn_01x04": "J",
    "regulator": "U", "ams1117": "U",
}


def _apply_text_offset(content: str, dx: float, dy: float) -> str:
    """Apply a uniform (dx, dy) offset to all coordinates in a schematic file.

    Uses regex text manipulation to preserve the exact file format while
    shifting coordinates. Handles (at x y [angle]) and (xy x y) patterns.
    Skips elements with negative coordinates (off-screen seeds).
    """

    def _shift_at(match: _re.Match) -> str:
        """Shift (at x y [angle]) or (xy x y) coordinates."""
        prefix = match.group(1)  # "(at " or "(xy "
        x = float(match.group(2))
        y = float(match.group(3))
        rest = match.group(4)  # " angle)" or ")"

        # Skip off-screen elements
        if x < -100 or y < -100:
            return match.group(0)

        new_x = round(x + dx, 2)
        new_y = round(y + dy, 2)
        return f"{prefix}{new_x} {new_y}{rest}"

    # Match (at x y [angle]) and (xy x y) patterns
    # Captures: prefix "(at " or "(xy ", x value, y value, rest
    pattern = r'(\(at )(-?[\d.]+)\s+(-?[\d.]+)([\s\)].*)'
    content = _re.sub(pattern, _shift_at, content)

    pattern_xy = r'(\(xy )(-?[\d.]+)\s+(-?[\d.]+)(\))'
    content = _re.sub(pattern_xy, _shift_at, content)

    return content

# Max distance (mm) to draw a wire between two pins; beyond this, rely on labels
WIRE_PROXIMITY = 30.0


class SchematicEngine:
    """Generates KiCAD schematics from circuit definitions."""

    def __init__(
        self,
        template_path: Optional[Path] = None,
        project_path: Optional[Path] = None,
    ):
        self.template_path = template_path or DEFAULT_TEMPLATE
        self.project_path = project_path
        self._dynamic_loader: Optional[DynamicSymbolLoader] = None
        self._pin_locator = PinLocator()
        self._placed: Dict[str, Any] = {}
        self._sch: Optional[Schematic] = None
        self._output_path: Optional[Path] = None
        self._placed_labels: set = set()
        self._wire_segments: List[Tuple[str, float, float, float, float]] = []

    @property
    def dynamic_loader(self) -> DynamicSymbolLoader:
        if self._dynamic_loader is None:
            self._dynamic_loader = DynamicSymbolLoader(project_path=self.project_path)
        return self._dynamic_loader

    def generate(
        self,
        circuit: Dict[str, Any],
        output_path: str | Path,
        validate: bool = True,
        paper_size: str = "A4",
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path = output_path
        self._placed = {}
        self._placed_labels = set()
        self._wire_segments = []

        import shutil
        shutil.copy2(self.template_path, output_path)

        self._sch = Schematic(str(output_path))

        components = circuit.get("components", [])
        nets = circuit.get("nets", [])

        self._auto_assign_references(components)

        # Separate seed components from dynamic (non-seed) components
        seed_components = []
        dynamic_components = []
        for comp in components:
            type_str = comp.get("type", "R")
            lib, sym_name = self._resolve_type(type_str)
            seed = self._find_seed(lib, sym_name)
            if seed is not None:
                seed_components.append(comp)
            else:
                dynamic_components.append(comp)

        # Place all seed components first
        for comp in seed_components:
            self._place_component(comp)

        # Place all dynamic components in a single batch to avoid
        # the kicad-skip write overwriting previous dynamic injections
        if dynamic_components:
            self._place_dynamic_batch(dynamic_components)

        for net in nets:
            self._pre_register_label_stubs(net)

        for net in nets:
            self._route_net(net)

        self._place_power_flags(nets)

        self._cleanup_seeds()
        self._fix_dnp_flags()

        # Preserve dynamic symbols that were injected via text manipulation.
        # kicad-skip's write() only knows about objects in its internal model,
        # so dynamically injected lib_symbols and instances would be lost.
        # We snapshot the file content BEFORE write, then merge the dynamic
        # parts back in AFTER write.
        dynamic_lib_symbols = self._extract_dynamic_lib_symbols(output_path)
        dynamic_instances = self._extract_dynamic_instances(output_path)

        self._sch.write(str(output_path))

        # Merge dynamic symbols back into the kicad-skip-written file.
        # Only merge lib_symbols (definitions), NOT instances — kicad-skip
        # already preserves dynamically-injected instances in its model, so
        # merging them again creates duplicates that cause pin_to_pin ERC errors.
        if dynamic_lib_symbols or dynamic_instances:
            self._merge_dynamic_symbols(
                output_path, dynamic_lib_symbols, ""
            )

        self._scrub_seed_text(output_path)
        self._inject_power_units(output_path)
        self._center_on_sheet(output_path, paper_name=paper_size)

        if validate:
            self._post_process(output_path)

        self._ensure_project_file(output_path)

        logger.info(f"Generated schematic: {output_path}")
        return output_path

    @staticmethod
    def _ensure_project_file(sch_path: Path) -> None:
        """Create a JSON .kicad_pro (KiCAD 8 format) if one doesn't exist."""
        import json
        pro_path = sch_path.with_suffix(".kicad_pro")
        if pro_path.exists():
            return
        sch_filename = sch_path.name
        pcb_filename = sch_path.with_suffix(".kicad_pcb").name
        pro_filename = pro_path.name
        project_data = {
            "board": {
                "design_settings": {
                    "defaults": {},
                    "diff_pair_dimensions": [],
                    "drc_exclusions": [],
                    "rules": {},
                    "track_widths": [],
                    "via_dimensions": [],
                },
                "filename": pcb_filename,
            },
            "boards": [],
            "libraries": {
                "pinned_footprint_libs": [],
                "pinned_symbol_libs": [],
            },
            "meta": {
                "filename": pro_filename,
                "version": 1,
            },
            "net_settings": {
                "classes": [],
                "meta": {"version": 0},
            },
            "pcbnew": {
                "page_layout_descr_file": "",
            },
            "sheets": [["root", sch_filename]],
            "text_variables": {},
        }
        pro_path.write_text(
            json.dumps(project_data, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info(f"Created project file: {pro_path}")

    @staticmethod
    def _auto_assign_references(components: List[Dict[str, Any]]) -> None:
        """Auto-number component references if bare type prefix given."""
        counters: Dict[str, int] = {}
        for comp in components:
            ref = comp.get("ref", "")
            type_str = comp.get("type", "R").lower().strip()
            prefix = REF_PREFIXES.get(type_str, "")
            if prefix and ref.strip().upper() == prefix.upper():
                counters[prefix] = counters.get(prefix, 0) + 1
                comp["ref"] = f"{prefix}{counters[prefix]}"

    def _resolve_type(self, type_str: str) -> Tuple[str, str]:
        key = type_str.lower().strip()
        # Qualified library names (e.g. "MCU_Microchip_ATmega:ATmega328P-A")
        # must be handled FIRST to avoid substring matching in TYPE_MAP
        if ":" in type_str:
            lib, sym = type_str.split(":", 1)
            return lib, sym
        if key in TYPE_MAP:
            return TYPE_MAP[key][0], TYPE_MAP[key][1]
        for k, (lib, sym, _) in TYPE_MAP.items():
            if key == k:
                return lib, sym
        raise ValueError(f"Unknown component type: '{type_str}'")

    def _find_seed(self, lib: str, sym: str) -> Optional[Any]:
        full_id = f"{lib}:{sym}"
        for s in self._sch.symbol:
            if s.lib_id.value == full_id:
                return s
        return None

    def _place_component(self, comp: Dict[str, Any]) -> None:
        ref = comp["ref"]
        type_str = comp.get("type", "R")
        value = comp.get("value", "")
        footprint = comp.get("footprint", "") or FOOTPRINT_MAP.get(
            type_str.lower().strip(), ""
        )
        x = comp.get("x", 50.0)
        y = comp.get("y", 50.0)
        rotation = comp.get("rotation", 0)

        lib, sym_name = self._resolve_type(type_str)
        seed = self._find_seed(lib, sym_name)

        if seed is not None:
            clone = seed.clone()
            clone.property.Reference.value = ref
            if value:
                clone.property.Value.value = value
            if footprint:
                clone.property.Footprint.value = footprint
            clone.move(x, y)
            if rotation != 0:
                clone.at.value[2] = rotation

            if hasattr(clone, "dnp"):
                clone.dnp.value = False
            if hasattr(clone, "in_bom"):
                clone.in_bom.value = True
            if hasattr(clone, "on_board"):
                clone.on_board.value = True

            self._placed[ref] = clone
            logger.debug(f"Cloned {lib}:{sym_name} as {ref} at ({x}, {y})")

    def _place_dynamic_batch(self, components: List[Dict[str, Any]]) -> None:
        """Place all dynamic (non-seed) components in a single batch.

        This avoids the kicad-skip overwrite problem where each individual
        dynamic placement would cause a save/reload cycle that loses
        previously injected dynamic symbols.
        """
        # Save current seed state
        self._sch.write(str(self._output_path))

        # Inject all dynamic symbols and instances in one pass
        for comp in components:
            ref = comp["ref"]
            type_str = comp.get("type", "R")
            value = comp.get("value", "")
            footprint = comp.get("footprint", "") or FOOTPRINT_MAP.get(
                type_str.lower().strip(), ""
            )
            x = comp.get("x", 50.0)
            y = comp.get("y", 50.0)

            lib, sym_name = self._resolve_type(type_str)

            self.dynamic_loader.add_component(
                schematic_path=self._output_path,
                library_name=lib,
                symbol_name=sym_name,
                reference=ref,
                value=value,
                footprint=footprint,
                x=x,
                y=y,
            )
            logger.debug(f"Dynamically loaded {lib}:{sym_name} as {ref}")

        # Reload once to pick up all dynamic components
        self._sch = Schematic(str(self._output_path))
        for s in self._sch.symbol:
            ref = s.property.Reference.value
            if ref in {c["ref"] for c in components}:
                self._placed[ref] = s

    def _get_pin_info(self, ref: str, pin_id: str) -> Optional[Dict]:
        """Return pin position, direction (degrees), and electric type."""
        if ref not in self._placed:
            return None
        symbol = self._placed[ref]
        sym_at = symbol.at.value
        sx, sy, srot = float(sym_at[0]), float(sym_at[1]), float(sym_at[2])
        if not hasattr(symbol, "lib_symbol") or symbol.lib_symbol is None:
            # Fallback for dynamically loaded symbols: resolve pins from the file
            if self._output_path and self._output_path.exists():
                return self._get_pin_info_from_file(ref, pin_id)
            return None
        for lp in symbol.lib_symbol.pin:
            pin_num = str(lp.number.value) if hasattr(lp.number, "value") else str(lp.number)
            pin_name = str(lp.name.value) if hasattr(lp.name, "value") else str(lp.name)
            if pin_id not in (pin_num, pin_name):
                continue
            lp_at = lp.at.value
            px, py, pdir = float(lp_at[0]), float(lp_at[1]), float(lp_at[2])
            py = -py
            rad = math.radians(srot)
            rx = px * math.cos(rad) - py * math.sin(rad)
            ry = px * math.sin(rad) + py * math.cos(rad)
            # Y-flip mirrors direction: 90°<->270°, 0° and 180° unchanged
            abs_dir = (360 - ((pdir + srot) % 360)) % 360
            electric_type = ""
            raw = lp.raw
            if isinstance(raw, list) and len(raw) > 1:
                electric_type = str(raw[1])
            return {
                "pos": [round(sx + rx, 4), round(sy + ry, 4)],
                "dir": abs_dir,
                "etype": electric_type,
            }
        return None

    def _get_pin_info_from_file(self, ref: str, pin_id: str) -> Optional[Dict]:
        """Resolve pin info for dynamically loaded symbols using the PinLocator."""
        try:
            loc = self._pin_locator.get_pin_location(
                self._output_path, ref, pin_id
            )
            angle = self._pin_locator.get_pin_angle(
                self._output_path, ref, pin_id
            )
            if loc and angle is not None:
                return {
                    "pos": [round(loc[0], 4), round(loc[1], 4)],
                    "dir": angle,
                    "etype": "passive",
                }
        except Exception as e:
            logger.debug(f"PinLocator fallback failed for {ref}.{pin_id}: {e}")
        return None

    def _get_pin_position(self, ref: str, pin_id: str) -> Optional[List[float]]:
        info = self._get_pin_info(ref, pin_id)
        return info["pos"] if info else None

    def _get_all_pins(self, ref: str) -> Dict[str, List[float]]:
        symbol = self._placed.get(ref)
        if not symbol or not hasattr(symbol, "lib_symbol") or symbol.lib_symbol is None:
            return {}
        sym_at = symbol.at.value
        sx, sy, srot = float(sym_at[0]), float(sym_at[1]), float(sym_at[2])
        pins = {}
        for lp in symbol.lib_symbol.pin:
            pin_num = str(lp.number.value) if hasattr(lp.number, "value") else str(lp.number)
            pin_name = str(lp.name.value) if hasattr(lp.name, "value") else str(lp.name)
            lp_at = lp.at.value
            px, py = float(lp_at[0]), float(lp_at[1])
            py = -py
            rad = math.radians(srot)
            rx = px * math.cos(rad) - py * math.sin(rad)
            ry = px * math.sin(rad) + py * math.cos(rad)
            pos = [round(sx + rx, 4), round(sy + ry, 4)]
            pins[pin_num] = pos
            if pin_name != "~":
                pins[pin_name] = pos
        return pins

    def _pre_register_label_stubs(self, net: Dict[str, Any]) -> None:
        """Pre-compute label stub positions and register them for crossing detection."""
        net_name = net["name"]
        for pr in net.get("pins", []):
            info = self._get_pin_info(pr["ref"], pr["pin"])
            if not info:
                continue
            pos = info["pos"]
            dx, dy, _ = self._label_offset_for_direction(info["dir"])
            label_pos = [round(pos[0] + dx, 2), round(pos[1] + dy, 2)]
            self._wire_segments.append(
                (net_name, pos[0], pos[1], label_pos[0], label_pos[1])
            )

    def _route_net(self, net: Dict[str, Any]) -> None:
        """Route a net: per-pin labels for connectivity, short wires for nearby aligned pins."""
        net_name = net["name"]
        pin_refs = net.get("pins", [])
        if not pin_refs:
            return

        resolved: List[Dict[str, Any]] = []
        for pr in pin_refs:
            ref, pin_id = pr["ref"], pr["pin"]
            info = self._get_pin_info(ref, pin_id)
            if info:
                resolved.append(info)
            else:
                logger.warning(f"Could not resolve pin {ref}.{pin_id}")

        if not resolved:
            return

        # Place a label at every pin for net connectivity
        for info in resolved:
            self._add_oriented_label(net_name, info["pos"], info["dir"])

        # Draw short wires only between axis-aligned nearby pins (< 25mm)
        MAX_WIRE_LEN = 25.0
        for i in range(len(resolved)):
            for j in range(i + 1, len(resolved)):
                p1 = resolved[i]["pos"]
                p2 = resolved[j]["pos"]
                dx = abs(p1[0] - p2[0])
                dy = abs(p1[1] - p2[1])
                aligned = dx < 0.01 or dy < 0.01
                dist = math.hypot(dx, dy)
                if aligned and dist < MAX_WIRE_LEN and dist > 0.01:
                    seg = (net_name, p1[0], p1[1], p2[0], p2[1])
                    if not self._would_cross(seg):
                        self._commit_segment(seg)

    def _try_draw_manhattan(
        self, net_name: str, start: List[float], end: List[float]
    ) -> bool:
        """Draw an orthogonal (L-shaped) wire without crossing other nets."""
        sx, sy = start
        ex, ey = end

        if abs(sx - ex) < 0.01:
            seg = (net_name, sx, sy, ex, ey)
            if not self._would_cross(seg):
                self._commit_segment(seg)
                return True
            return False

        if abs(sy - ey) < 0.01:
            seg = (net_name, sx, sy, ex, ey)
            if not self._would_cross(seg):
                self._commit_segment(seg)
                return True
            return False

        seg_a1 = (net_name, sx, sy, ex, sy)
        seg_a2 = (net_name, ex, sy, ex, ey)
        if not self._would_cross(seg_a1) and not self._would_cross(seg_a2):
            self._commit_segment(seg_a1)
            self._commit_segment(seg_a2)
            return True

        seg_b1 = (net_name, sx, sy, sx, ey)
        seg_b2 = (net_name, sx, ey, ex, ey)
        if not self._would_cross(seg_b1) and not self._would_cross(seg_b2):
            self._commit_segment(seg_b1)
            self._commit_segment(seg_b2)
            return True

        return False

    def _would_cross(
        self, new_seg: Tuple[str, float, float, float, float]
    ) -> bool:
        """Check if a new wire segment would cross any existing different-net segment."""
        new_net = new_seg[0]
        for existing in self._wire_segments:
            if existing[0] == new_net:
                continue
            if self._segments_intersect(new_seg, existing):
                return True
        return False

    @staticmethod
    def _segments_intersect(
        seg1: Tuple[str, float, float, float, float],
        seg2: Tuple[str, float, float, float, float],
    ) -> bool:
        """Check if two line segments share any interior point."""
        _, x1a, y1a, x2a, y2a = seg1
        _, x1b, y1b, x2b, y2b = seg2

        ax1, ax2 = min(x1a, x2a), max(x1a, x2a)
        ay1, ay2 = min(y1a, y2a), max(y1a, y2a)
        bx1, bx2 = min(x1b, x2b), max(x1b, x2b)
        by1, by2 = min(y1b, y2b), max(y1b, y2b)

        tol = 0.5

        a_h = (ay2 - ay1) < tol
        a_v = (ax2 - ax1) < tol
        b_h = (by2 - by1) < tol
        b_v = (bx2 - bx1) < tol

        if a_h and b_v:
            return (
                bx1 > ax1 + tol
                and bx1 < ax2 - tol
                and ay1 > by1 + tol
                and ay1 < by2 - tol
            )
        if a_v and b_h:
            return (
                ax1 > bx1 + tol
                and ax1 < bx2 - tol
                and by1 > ay1 + tol
                and by1 < ay2 - tol
            )
        if a_h and b_h and abs(ay1 - by1) < tol:
            return ax1 < bx2 - tol and bx1 < ax2 - tol
        if a_v and b_v and abs(ax1 - bx1) < tol:
            return ay1 < by2 - tol and by1 < ay2 - tol
        return False

    def _commit_segment(
        self, seg: Tuple[str, float, float, float, float]
    ) -> None:
        """Draw a wire segment and record it for crossing detection."""
        _, x1, y1, x2, y2 = seg
        w = self._sch.wire.new()
        w.start_at([x1, y1])
        w.end_at([x2, y2])
        self._wire_segments.append(seg)

    def _label_offset_for_direction(self, pin_dir: float) -> Tuple[float, float, int]:
        """Compute (dx, dy, label_rotation) so the label is AWAY from the body."""
        offset = LABEL_OFFSET
        if 45 <= pin_dir < 135:      # pin extends DOWN → label goes UP
            return 0.0, -offset, 90
        elif 135 <= pin_dir < 225:    # pin extends LEFT → label goes RIGHT
            return offset, 0.0, 0
        elif 225 <= pin_dir < 315:    # pin extends UP → label goes DOWN
            return 0.0, offset, 270
        else:                         # pin extends RIGHT → label goes LEFT
            return -offset, 0.0, 180

    def _add_oriented_label(self, name: str, pos: List[float], pin_dir: float) -> None:
        """Place a global label at a pin position oriented away from the component."""
        key = (name, round(pos[0], 2), round(pos[1], 2))
        if key in self._placed_labels:
            return
        self._placed_labels.add(key)

        dx, dy, label_rot = self._label_offset_for_direction(pin_dir)
        label_pos = [round(pos[0] + dx, 2), round(pos[1] + dy, 2)]

        label = self._sch.global_label.new()
        label.at.value = [float(f"{label_pos[0]:.2f}"), float(f"{label_pos[1]:.2f}"), label_rot]
        label.value = name

        w = self._sch.wire.new()
        w.start_at(pos)
        w.end_at(label_pos)

    def _extract_dynamic_lib_symbols(self, path: Path) -> str:
        """Extract lib_symbol blocks that are NOT in the seed template."""
        SEED_SYMBOLS = {
            "Device:R", "Device:C", "Device:L", "Device:Crystal",
            "Device:D", "Device:LED", "Device:Q_NPN", "Device:Q_NMOS",
            "Amplifier_Operational:LM358", "Connector_Generic:Conn_01x02",
            "Connector_Generic:Conn_01x04", "Regulator_Linear:AMS1117-3.3",
            "Switch:SW_Push", "power:PWR_FLAG",
        }
        content = path.read_text(encoding="utf-8")
        dynamic_blocks = []

        # Find lib_symbols section
        lib_start = content.find("(lib_symbols")
        if lib_start < 0:
            return ""
        depth = 0
        lib_end = lib_start
        for i in range(lib_start, len(content)):
            if content[i] == "(":
                depth += 1
            elif content[i] == ")":
                depth -= 1
                if depth == 0:
                    lib_end = i
                    break

        lib_section = content[lib_start:lib_end + 1]

        # Extract each (symbol "Lib:Name" ...) block
        for seed_name in SEED_SYMBOLS:
            pass  # We'll filter below

        # Find all symbol blocks in lib_symbols
        i = len("(lib_symbols ")
        while i < len(lib_section) - 1:
            rest = lib_section[i:]
            if not rest.strip().startswith("(symbol "):
                i += 1
                continue

            # Find the symbol name
            name_match = re.match(r'\(symbol\s+"([^"]+)"', rest)
            if not name_match:
                i += 1
                continue

            sym_name = name_match.group(1)
            # Find the balanced block
            depth = 0
            block_start = 0
            block_end = 0
            for j in range(len(rest)):
                if rest[j] == "(":
                    depth += 1
                elif rest[j] == ")":
                    depth -= 1
                    if depth == 0:
                        block_end = j + 1
                        break

            block = rest[block_start:block_end]
            if sym_name not in SEED_SYMBOLS:
                dynamic_blocks.append(block)

            i += block_end

        return "\n    ".join(dynamic_blocks)

    def _extract_dynamic_instances(self, path: Path) -> str:
        """Extract component instances that are NOT seed-based (i.e., dynamically loaded)."""
        content = path.read_text(encoding="utf-8")
        SEED_LIB_IDS = {
            "Device:R", "Device:C", "Device:L", "Device:Crystal",
            "Device:D", "Device:LED", "Device:Q_NPN", "Device:Q_NMOS",
            "Amplifier_Operational:LM358", "Connector_Generic:Conn_01x02",
            "Connector_Generic:Conn_01x04", "Regulator_Linear:AMS1117-3.3",
            "Switch:SW_Push", "power:PWR_FLAG",
        }

        dynamic_blocks = []

        # Find all top-level (symbol (lib_id "Lib:Name") ...) blocks
        i = 0
        n = len(content)
        while i < n:
            if content[i:i + 8] == "(symbol ":
                # Check if this has a lib_id
                chunk = content[i:i + 500]
                lib_match = re.search(r'\(lib_id\s+"([^"]+)"', chunk)
                if lib_match:
                    lib_id = lib_match.group(1)
                    if lib_id not in SEED_LIB_IDS:
                        # Find balanced block
                        depth = 0
                        j = i
                        while j < n:
                            if content[j] == "(":
                                depth += 1
                            elif content[j] == ")":
                                depth -= 1
                                if depth == 0:
                                    break
                            j += 1
                        if j < n:
                            dynamic_blocks.append(content[i:j + 1])
                            i = j + 1
                            continue
            i += 1

        return "\n  ".join(dynamic_blocks)

    def _merge_dynamic_symbols(
        self, path: Path, lib_blocks: str, inst_blocks: str
    ) -> None:
        """Merge dynamic lib_symbol definitions and instances back into the file."""
        content = path.read_text(encoding="utf-8")

        # 1. Inject dynamic lib_symbols into the lib_symbols section
        if lib_blocks:
            lib_end_marker = ") (sheet_instances"
            if lib_end_marker not in content:
                lib_end_marker = ")(sheet_instances"
            # Find the closing ) of lib_symbols section
            lib_start = content.find("(lib_symbols")
            if lib_start >= 0:
                depth = 0
                lib_end = lib_start
                for i in range(lib_start, len(content)):
                    if content[i] == "(":
                        depth += 1
                    elif content[i] == ")":
                        depth -= 1
                        if depth == 0:
                            lib_end = i
                            break
                # Insert just before the closing )
                content = (
                    content[:lib_end]
                    + "\n    " + lib_blocks + "\n  "
                    + content[lib_end:]
                )
                logger.info(f"Merged {len(lib_blocks)} chars of dynamic lib_symbols")

        # 2. Inject dynamic instances before sheet_instances
        if inst_blocks:
            marker = "(sheet_instances"
            insert_at = content.rfind(marker)
            if insert_at >= 0:
                content = (
                    content[:insert_at]
                    + inst_blocks + "\n  "
                    + content[insert_at:]
                )
                logger.info(f"Merged {len(inst_blocks)} chars of dynamic instances")

        path.write_text(content, encoding="utf-8")

    def _place_power_flags(self, nets: List[Dict[str, Any]]) -> None:
        """Place PWR_FLAG symbols offset from labels to avoid overlap."""
        pwr_seed = self._find_seed("power", "PWR_FLAG")
        if pwr_seed is None:
            logger.debug("No PWR_FLAG seed available, skipping power flags")
            return

        GROUND_NAMES = {"GND", "VSS", "DGND", "AGND", "PGND", "SGND"}
        flagged_nets: set = set()
        flag_count = 0

        all_net_pins: Dict[str, List[Dict]] = {}
        for net in nets:
            nn = net.get("name", "")
            all_net_pins.setdefault(nn, []).extend(net.get("pins", []))

        for net in nets:
            net_name = net.get("name", "")
            if net_name in flagged_nets:
                continue

            name_upper = net_name.upper().lstrip("+")
            is_ground = name_upper in GROUND_NAMES

            has_power_output = False
            has_power_input = False
            for pr in all_net_pins.get(net_name, []):
                info = self._get_pin_info(pr["ref"], pr["pin"])
                if info:
                    et = info["etype"]
                    if et == "power_out":
                        has_power_output = True
                    if et == "power_in":
                        has_power_input = True

            if has_power_output:
                continue
            if not (is_ground or has_power_input):
                continue

            pin_refs = net.get("pins", [])
            if not pin_refs:
                continue

            first_pin = pin_refs[0]
            info = self._get_pin_info(first_pin["ref"], first_pin["pin"])
            if info is None:
                continue
            pos = info["pos"]
            pin_dir = info["dir"]

            # Place PWR_FLAG beyond the label to avoid overlap
            dx, dy, _ = self._label_offset_for_direction(pin_dir)
            label_x = round(pos[0] + dx, 2)
            label_y = round(pos[1] + dy, 2)
            flag_x = round(pos[0] + dx * 2, 2)
            flag_y = round(pos[1] + dy * 2, 2)

            clone = pwr_seed.clone()
            clone.property.Reference.value = f"#FLG{flag_count + 1:03d}"
            clone.property.Value.value = "PWR_FLAG"
            clone.move(flag_x, flag_y)

            if hasattr(clone, "dnp"):
                clone.dnp.value = False
            if hasattr(clone, "in_bom"):
                clone.in_bom.value = False
            if hasattr(clone, "on_board"):
                clone.on_board.value = True

            # Wire from label position to PWR_FLAG if label exists; otherwise from pin
            label_key = (net_name, round(pos[0], 2), round(pos[1], 2))
            w = self._sch.wire.new()
            if label_key in self._placed_labels:
                w.start_at([label_x, label_y])
            else:
                w.start_at(pos)
            w.end_at([flag_x, flag_y])

            flagged_nets.add(net_name)
            flag_count += 1
            logger.debug(f"Placed PWR_FLAG for {net_name} at ({flag_x}, {flag_y})")

    # ------------------------------------------------------------------
    # Sheet centering
    # ------------------------------------------------------------------

    def _center_on_sheet(self, path: Path, paper_name: str = "A4") -> None:
        """Center all schematic elements within the sheet boundaries.

        Uses regex-based text manipulation to preserve the exact file format
        while translating all coordinates by a uniform offset.
        """
        from engine.backends.sheet_bounds import (
            compute_centering_offset,
            compute_circuit_bbox,
            get_paper_size,
            select_paper_size,
        )

        bbox = compute_circuit_bbox(path)
        min_x, min_y, max_x, max_y = bbox

        # Guard: skip if no elements found or degenerate bbox
        if max_x - min_x < 0.1 and max_y - min_y < 0.1:
            logger.debug("Skipping centering: no significant circuit geometry found")
            return

        # Auto-scale paper if circuit doesn't fit
        resolved_paper = select_paper_size(bbox, preferred=paper_name)
        paper = get_paper_size(resolved_paper)

        # Compute centering offset
        dx, dy = compute_centering_offset(bbox, paper)

        # Skip if already well-centered (< 0.5mm offset)
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            logger.debug("Circuit already centered, skipping offset")
            return

        # Apply offset via regex text manipulation (preserves file format)
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()

        # Update paper header
        content = _re.sub(
            r'\(paper\s+"[^"]*"\)',
            f'(paper "{resolved_paper}")',
            content,
        )

        # Apply dx/dy offset to all coordinate pairs in (at ...) and (xy ...)
        # We use a two-pass approach:
        # 1. Shift all (at x y [angle]) coordinates
        # 2. Shift all (xy x y) coordinates inside wires
        content = _apply_text_offset(content, dx, dy)

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

        logger.info(
            f"Centered circuit on {resolved_paper}: offset ({dx:.2f}, {dy:.2f}) mm, "
            f"bbox ({min_x:.1f},{min_y:.1f})-({max_x:.1f},{max_y:.1f}) → "
            f"({min_x+dx:.1f},{min_y+dy:.1f})-({max_x+dx:.1f},{max_y+dy:.1f})"
        )

    @staticmethod
    def _update_paper_header(data: list, paper_name: str) -> None:
        """Update the (paper "...") header in the schematic sexp data."""
        for i, item in enumerate(data):
            if isinstance(item, list) and item and item[0] == Symbol("paper"):
                data[i] = [Symbol("paper"), paper_name]
                return

    @staticmethod
    def _apply_offset(data: list, dx: float, dy: float) -> None:
        """Apply a uniform (dx, dy) offset to all coordinate-bearing elements."""
        # Tags that represent top-level schematic elements we want to shift
        _LABEL_TAGS = {
            Symbol("global_label"),
            Symbol("label"),
            Symbol("hierarchical_label"),
            Symbol("net_tie"),
        }

        for item in data:
            if not isinstance(item, list) or not item:
                continue

            tag = item[0]

            # --- Placed symbol instances: (symbol (lib_id "...") ...) ---
            if tag == Symbol("symbol"):
                has_lib_id = any(
                    isinstance(sub, list) and sub and sub[0] == Symbol("lib_id")
                    for sub in item[1:]
                )
                if has_lib_id:
                    SchematicEngine._shift_symbol(item, dx, dy)

            # --- Wires: (wire (pts (xy x y) (xy x y))) ---
            elif tag == Symbol("wire"):
                SchematicEngine._shift_wire(item, dx, dy)

            # --- Labels, junctions, no_connect, text ---
            elif tag in _LABEL_TAGS or tag in (
                Symbol("junction"),
                Symbol("no_connect"),
                Symbol("text"),
            ):
                SchematicEngine._shift_at(item, dx, dy)

    @staticmethod
    def _shift_symbol(sexp: list, dx: float, dy: float) -> None:
        """Shift a placed symbol instance and its property positions."""
        shifted_main = False
        for sub in sexp[1:]:
            if not isinstance(sub, list) or not sub:
                continue

            # First (at ...) is the symbol position
            if sub[0] == Symbol("at") and len(sub) >= 3 and not shifted_main:
                x, y = float(sub[1]), float(sub[2])
                if x > -100 and y > -100:
                    sub[1] = x + dx
                    sub[2] = y + dy
                shifted_main = True

            # Properties have their own (at x y ...)
            elif sub[0] == Symbol("property"):
                for prop_sub in sub[1:]:
                    if (
                        isinstance(prop_sub, list)
                        and prop_sub
                        and prop_sub[0] == Symbol("at")
                        and len(prop_sub) >= 3
                    ):
                        px, py = float(prop_sub[1]), float(prop_sub[2])
                        if px > -100 and py > -100:
                            prop_sub[1] = px + dx
                            prop_sub[2] = py + dy

    @staticmethod
    def _shift_wire(sexp: list, dx: float, dy: float) -> None:
        """Shift wire endpoint coordinates."""
        for sub in sexp[1:]:
            if isinstance(sub, list) and sub and sub[0] == Symbol("pts"):
                for pt in sub[1:]:
                    if (
                        isinstance(pt, list)
                        and pt
                        and pt[0] == Symbol("xy")
                        and len(pt) >= 3
                    ):
                        pt[1] = float(pt[1]) + dx
                        pt[2] = float(pt[2]) + dy

    @staticmethod
    def _shift_at(sexp: list, dx: float, dy: float) -> None:
        """Shift (at x y [angle]) coordinates in any element."""
        for sub in sexp[1:]:
            if isinstance(sub, list) and sub and sub[0] == Symbol("at") and len(sub) >= 3:
                x, y = float(sub[1]), float(sub[2])
                if x > -100 and y > -100:
                    sub[1] = x + dx
                    sub[2] = y + dy
                break  # Only shift first (at ...)

    def _get_pin_electric_type(self, ref: str, pin_id: str) -> str:
        info = self._get_pin_info(ref, pin_id)
        return info["etype"] if info else ""

    def _cleanup_seeds(self) -> None:
        to_delete = []
        for sym in self._sch.symbol:
            ref = sym.property.Reference.value
            if ref.startswith("_SEED_"):
                to_delete.append(sym)
        for sym in to_delete:
            sym.delete()

    def _scrub_seed_text(self, path: Path) -> None:
        """Remove seed blocks, fix instance references, and update project name."""
        import re as _re
        import uuid as _uuid

        content = path.read_text(encoding="utf-8")

        # Generate a unique schematic UUID (KiCAD expects per-file unique UUIDs)
        sch_uuid = str(_uuid.uuid4())
        content = _re.sub(
            r'\(uuid\s+"?[0-9a-f-]+"?\)',
            f'(uuid "{sch_uuid}")',
            content,
            count=1,
        )

        # Fix generator string so KiCAD treats the file as native
        content = _re.sub(
            r'\(generator\s+"[^"]*"\)',
            '(generator "eeschema")',
            content,
        )

        # Fix project name in instances blocks to match the output file stem
        import re as _name_re
        project_name = _name_re.sub(r'[^a-zA-Z0-9_-]', '_', path.stem)
        content = _re.sub(
            r'\(project\s+"project"',
            f'(project "{project_name}"',
            content,
        )

        # Fix instances path: KiCAD 9 requires "/<schematic-UUID>" not just "/"
        # Only replace paths followed by (reference — not sheet_instances paths with (page
        content = _re.sub(
            r'\(path\s+"/"\s+\(reference',
            f'(path "/{sch_uuid}" (reference',
            content,
        )

        if "_SEED_" not in content:
            path.write_text(content, encoding="utf-8")
            return

        result = []
        i = 0
        n = len(content)
        while i < n:
            if content[i:i + 8] == "(symbol ":
                depth = 0
                j = i
                while j < n:
                    if content[j] == "(":
                        depth += 1
                    elif content[j] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1

                if j < n:
                    block = content[i:j + 1]

                    if "(at -200 " in block:
                        logger.debug(f"Scrubbed seed block at position {i}")
                        i = j + 1
                        continue

                    if "_SEED_" in block:
                        ref_match = _re.search(
                            r'\(property\s+"Reference"\s+"([^"]+)"', block
                        )
                        if ref_match and not ref_match.group(1).startswith("_SEED_"):
                            actual_ref = ref_match.group(1)
                            block = _re.sub(
                                r'\(reference\s+"_SEED_[^"]*"\)',
                                f'(reference "{actual_ref}")',
                                block,
                            )
                            logger.debug(f"Fixed instances ref -> {actual_ref}")

                    result.append(block)
                    i = j + 1
                    continue

            result.append(content[i])
            i += 1

        cleaned = "".join(result)
        if cleaned != content:
            path.write_text(cleaned, encoding="utf-8")
            logger.info(f"Scrubbed seed remnants from {path.name}")

    @staticmethod
    def _inject_power_units(path: Path) -> None:
        """Add power-unit symbol instances for multi-unit components (e.g. LM358 unit 3)."""
        import re as _re
        import uuid as _uuid

        MULTI_UNIT_SYMS = {
            "Amplifier_Operational:LM358": 3,
        }

        content = path.read_text(encoding="utf-8")
        additions = []

        for lib_sym, power_unit in MULTI_UNIT_SYMS.items():
            inst_pattern = (
                rf'\(symbol\s+\(lib_id\s+"{_re.escape(lib_sym)}"\)'
                r'\s+\(at\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\)'
                r'\s+\(unit\s+1\)'
            )
            for m in _re.finditer(inst_pattern, content):
                x, y, rot = m.group(1), m.group(2), m.group(3)
                start = m.start()
                chunk = content[start:start + 3000]

                uuid_m = _re.search(r'\(uuid\s+"([^"]+)"\)', chunk)
                ref_m = _re.search(r'\(reference\s+"([^"]+)"\)', chunk)
                proj_m = _re.search(r'\(project\s+"([^"]+)"', chunk)
                path_m = _re.search(r'\(path\s+"([^"]+)"', chunk)

                if not all([uuid_m, ref_m, proj_m, path_m]):
                    continue

                ref = ref_m.group(1)
                project_name = proj_m.group(1)
                sch_path = path_m.group(1).lstrip("/")

                new_uuid = str(_uuid.uuid4())
                power_block = (
                    f'\n(symbol (lib_id "{lib_sym}") '
                    f'(at {x} {y} {rot}) '
                    f'(unit {power_unit}) '
                    f'(in_bom yes) (on_board yes) (dnp no) '
                    f'(uuid "{new_uuid}")\n'
                    f'  (property "Reference" "{ref}" '
                    f'(at {x} {float(y) - 2.54:.2f} {rot}) '
                    f'(effects (font (size 1.27 1.27))))\n'
                    f'  (property "Value" "LM358" '
                    f'(at {x} {float(y) + 2.54:.2f} {rot}) '
                    f'(effects (font (size 1.27 1.27))))\n'
                    f'  (property "Footprint" "" '
                    f'(at {x} {y} {rot}) '
                    f'(effects (font (size 1.27 1.27)) (hide yes)))\n'
                    f'  (property "Datasheet" "~" '
                    f'(at {x} {y} {rot}) '
                    f'(effects (font (size 1.27 1.27)) (hide yes)))\n'
                    f'  (instances (project "{project_name}" '
                    f'(path "/{sch_path}" '
                    f'(reference "{ref}") (unit {power_unit})))))\n'
                )
                additions.append(power_block)
                logger.info(
                    f"Added power unit {power_unit} for {lib_sym} ref={ref}"
                )

        if additions:
            insertion_point = content.rfind(")")
            if insertion_point > 0:
                content = (
                    content[:insertion_point]
                    + "".join(additions)
                    + content[insertion_point:]
                )
                path.write_text(content, encoding="utf-8")

    def _fix_dnp_flags(self) -> None:
        for sym in self._sch.symbol:
            ref = sym.property.Reference.value
            if not ref.startswith("_SEED_"):
                if hasattr(sym, "dnp") and sym.dnp is not None:
                    sym.dnp.value = False
                if hasattr(sym, "in_bom") and sym.in_bom is not None:
                    sym.in_bom.value = True
                if hasattr(sym, "on_board") and sym.on_board is not None:
                    sym.on_board.value = True

    @staticmethod
    def _clean_floats(path: Path) -> None:
        """Fix floating point artifacts in coordinate values within (at ...) and (xy ...) forms."""
        import re as _re
        content = path.read_text(encoding="utf-8")

        def _round_val(s):
            return f"{float(s):.2f}"

        def _round_pair(m):
            return f"{_round_val(m.group(1))} {_round_val(m.group(2))}"

        # Only round values inside (xy ...) or (at ...) S-expression forms
        cleaned = _re.sub(r'(?<=\(xy\s)([\d.]+)\s([\d.]+)', _round_pair, content)
        cleaned = _re.sub(r'(?<=\(at\s)([\d.]+)\s([\d.]+)', _round_pair, cleaned)

        if cleaned != content:
            path.write_text(cleaned, encoding="utf-8")

    def _post_process(self, path: Path) -> None:
        try:
            from engine.backends.schematic_snap import snap_to_grid
            snap_to_grid(path, grid_size=1.27, elements=["wires", "junctions", "labels"])
        except Exception as e:
            logger.warning(f"Grid snap failed (non-fatal): {e}")

        try:
            from engine.backends.schematic_analysis import find_orphaned_wires
            result = find_orphaned_wires(path)
            if result.get("count", 0) > 0:
                logger.warning(f"Orphaned wires detected: {result}")
        except Exception as e:
            logger.warning(f"Analysis failed (non-fatal): {e}")

        try:
            from engine.backends.validation import validate_schematic
            validation = validate_schematic(path)
            erc = validation.get("erc", {})
            if validation["passed"]:
                logger.info(f"ERC passed: {erc.get('errors', 0)} errors, {erc.get('warnings', 0)} warnings")
            else:
                for fail in validation.get("failures", []):
                    logger.warning(f"ERC validation issue: {fail}")
                logger.warning(f"ERC result: {erc.get('errors', 0)} errors, {erc.get('warnings', 0)} warnings")
        except Exception as e:
            logger.warning(f"ERC validation failed (non-fatal): {e}")

        self._clean_floats(path)
