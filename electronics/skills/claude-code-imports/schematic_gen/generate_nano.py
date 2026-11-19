#!/usr/bin/env python3
"""
Custom Arduino Nano schematic generator.

Handles complex dynamic symbols (ATmega328P-A, CH340G, USB_B_Micro, Conn_01x15)
by doing the full generation through text manipulation, bypassing kicad-skip's
limitations with dynamically loaded symbols.
"""

import json
import logging
import math
import re
import sys
import uuid
from pathlib import Path

# Add engine to path
sys.path.insert(0, str(Path(__file__).parent))
from engine.backends.dynamic_symbol_loader import DynamicSymbolLoader

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
SEED_TEMPLATE = TEMPLATES_DIR / "seed_template.kicad_sch"

LABEL_OFFSET = 7.62  # 3 * 2.54mm

# Seed symbols that exist in the template
SEED_SYMBOLS = {
    "Device:R", "Device:C", "Device:L", "Device:Crystal",
    "Device:D", "Device:LED", "Device:Q_NPN", "Device:Q_NMOS",
    "Amplifier_Operational:LM358", "Connector_Generic:Conn_01x02",
    "Connector_Generic:Conn_01x04", "Regulator_Linear:AMS1117-3.3",
    "Switch:SW_Push", "power:PWR_FLAG",
}

TYPE_MAP = {
    "r": "Device:R", "resistor": "Device:R",
    "c": "Device:C", "capacitor": "Device:C",
    "crystal": "Device:Crystal",
    "led": "Device:LED",
    "switch": "Switch:SW_Push", "sw_push": "Switch:SW_Push", "button": "Switch:SW_Push",
}

GRID = 2.54


def load_circuit(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def resolve_lib_id(type_str: str) -> str:
    """Resolve component type to full lib_id."""
    key = type_str.lower().strip()
    if key in TYPE_MAP:
        return TYPE_MAP[key]
    if ":" in type_str:
        return type_str
    return type_str


def is_seed_symbol(lib_id: str) -> bool:
    return lib_id in SEED_SYMBOLS


def extract_pins_from_lib_block(block: str) -> dict:
    """Extract pin definitions from a lib_symbol block text.
    Returns dict: pin_number -> {x, y, angle, length, name}
    """
    pins = {}
    for m in re.finditer(
        r'\(pin\s+\w+\s+\w+\s+\(at\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\)\s+\(length\s+([\d.]+)\)',
        block,
    ):
        pin_at_start = m.start()
        # Find the balanced (pin ...) block
        depth = 0
        j = pin_at_start
        while j < len(block):
            if block[j] == "(":
                depth += 1
            elif block[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        pin_block = block[pin_at_start : j + 1]

        name_m = re.search(r'\(name\s+"([^"]+)"', pin_block)
        num_m = re.search(r'\(number\s+"([^"]+)"', pin_block)
        if name_m and num_m:
            pin_num = num_m.group(1)
            pins[pin_num] = {
                "x": float(m.group(1)),
                "y": float(m.group(2)),
                "angle": float(m.group(3)),
                "length": float(m.group(4)),
                "name": name_m.group(1),
            }
    return pins


def get_pin_position(
    pin_data: dict, sym_x: float, sym_y: float, sym_rot: float
) -> tuple:
    """Get absolute pin position given symbol position/rotation.

    KiCAD symbol definitions use Y-up coordinates, but schematics use Y-down.
    The pin Y must be negated, and the pin direction must be mirrored accordingly.
    """
    px, py = pin_data["x"], -pin_data["y"]  # Y-flip: symbol -> schematic
    angle = pin_data["angle"]
    if sym_rot != 0:
        rad = math.radians(sym_rot)
        rx = px * math.cos(rad) - py * math.sin(rad)
        ry = px * math.sin(rad) + py * math.cos(rad)
        px, py = rx, ry
        angle = (360 - ((angle + sym_rot) % 360)) % 360  # Mirror direction
    return sym_x + px, sym_y + py, angle


def find_pin(pin_id: str, pins: dict) -> dict:
    """Find a pin by number or name."""
    if pin_id in pins:
        return pins[pin_id]
    for num, data in pins.items():
        if data["name"] == pin_id:
            return data
    return None


def make_label(name: str, x: float, y: float, rotation: int) -> str:
    """Create a global label S-expression."""
    return (
        f'    (global_label "{name}" (shape input) '
        f"(at {x:.2f} {y:.2f} {rotation}) "
        f'(fields_autoplaced)\n'
        f"      (uuid \"{uuid.uuid4()}\")\n"
        f'      (property "Intersheets" "" (at 0 0 0)\n'
        f"        (effects (font (size 1.27 1.27)) (hide yes)))\n"
        f"    )"
    )


def make_wire(x1: float, y1: float, x2: float, y2: float) -> str:
    """Create a wire S-expression."""
    return (
        f"    (wire (pts (xy {x1:.2f} {y1:.2f}) (xy {x2:.2f} {y2:.2f}))\n"
        f"      (uuid \"{uuid.uuid4()}\")\n"
        f"    )"
    )


def make_symbol_instance(
    lib_id: str, ref: str, value: str, footprint: str,
    x: float, y: float, rotation: float = 0, unit: int = 1,
) -> str:
    """Create a symbol instance S-expression."""
    return (
        f'  (symbol (lib_id "{lib_id}") (at {x:.2f} {y:.2f} {rotation}) (unit {unit})\n'
        f"    (in_bom yes) (on_board yes) (dnp no)\n"
        f'    (uuid "{uuid.uuid4()}")\n'
        f'    (property "Reference" "{ref}" (at {x:.2f} {y - 2.54:.2f} {rotation})\n'
        f"      (effects (font (size 1.27 1.27))))\n"
        f'    (property "Value" "{value}" (at {x:.2f} {y + 2.54:.2f} {rotation})\n'
        f"      (effects (font (size 1.27 1.27))))\n"
        f'    (property "Footprint" "{footprint}" (at {x:.2f} {y:.2f} {rotation})\n'
        f"      (effects (font (size 1.27 1.27)) (hide yes)))\n"
        f'    (property "Datasheet" "~" (at {x:.2f} {y:.2f} {rotation})\n'
        f"      (effects (font (size 1.27 1.27)) (hide yes)))\n"
        f"    (instances\n"
        f'      (project "arduino_nano"\n'
        f'        (path "/{uuid.uuid4()}"\n'
        f'          (reference "{ref}") (unit {unit})))))'
    )


def generate_arduino_nano(circuit: dict, output_path: str) -> None:
    """Generate the Arduino Nano schematic."""
    output = Path(output_path)
    loader = DynamicSymbolLoader()

    components = circuit["components"]
    nets = circuit["nets"]

    # Phase 1: Determine which symbols we need and load them
    lib_symbols_needed = {}  # lib_id -> raw text block
    component_pin_data = {}  # ref -> {pin_num: pin_data}

    for comp in components:
        lib_id = resolve_lib_id(comp["type"])
        comp["_lib_id"] = lib_id

        if lib_id not in lib_symbols_needed:
            if is_seed_symbol(lib_id):
                # We'll get seed symbols from the template
                lib_symbols_needed[lib_id] = None
            else:
                lib_name, sym_name = lib_id.split(":", 1)
                block = loader.extract_symbol_from_library(lib_name, sym_name)
                if block:
                    lib_symbols_needed[lib_id] = block
                    # Extract pins
                    pins = extract_pins_from_lib_block(block)
                    logger.info(f"Extracted {len(pins)} pins from {lib_id}")
                    for p in pins.values():
                        logger.debug(f"  Pin {p.get('name', '?')} at ({p['x']}, {p['y']})")
                else:
                    logger.error(f"Failed to load {lib_id}")

    # Phase 2: Build the schematic file from scratch
    # Start with the template to get the seed lib_symbols
    with open(SEED_TEMPLATE, "r") as f:
        template = f.read()

    # Extract seed lib_symbols section
    lib_start = template.find("(lib_symbols")
    depth = 0
    lib_end = lib_start
    for i in range(lib_start, len(template)):
        if template[i] == "(":
            depth += 1
        elif template[i] == ")":
            depth -= 1
            if depth == 0:
                lib_end = i
                break
    seed_lib_symbols = template[lib_start : lib_end + 1]

    # Phase 3: Extract pin data for ALL symbols (seed + dynamic)
    for comp in components:
        lib_id = comp["_lib_id"]
        ref = comp["ref"]

        if lib_id in lib_symbols_needed and lib_symbols_needed[lib_id] is not None:
            pins = extract_pins_from_lib_block(lib_symbols_needed[lib_id])
        else:
            # Parse from seed template
            seed_block_match = re.search(
                rf'\(symbol\s+"{re.escape(lib_id)}"', seed_lib_symbols
            )
            if seed_block_match:
                sb_start = seed_block_match.start()
                d = 0
                sb_end = sb_start
                for i in range(sb_start, len(seed_lib_symbols)):
                    if seed_lib_symbols[i] == "(":
                        d += 1
                    elif seed_lib_symbols[i] == ")":
                        d -= 1
                        if d == 0:
                            sb_end = i
                            break
                block = seed_lib_symbols[sb_start : sb_end + 1]
                pins = extract_pins_from_lib_block(block)
            else:
                logger.warning(f"No lib_symbol block found for {lib_id}")
                pins = {}

        component_pin_data[ref] = pins
        logger.info(f"{ref} ({lib_id}): {len(pins)} pins")

    # Phase 4: Create instances and resolve nets
    instances = []
    labels = []
    wires = []
    placed_labels = set()

    for comp in components:
        ref = comp["ref"]
        lib_id = comp["_lib_id"]
        value = comp.get("value", "")
        footprint = comp.get("footprint", "")
        x = comp.get("x", 50.8)
        y = comp.get("y", 50.8)
        rotation = comp.get("rotation", 0)

        inst = make_symbol_instance(lib_id, ref, value, footprint, x, y, rotation)
        instances.append(inst)

    # Resolve and route nets
    for net in nets:
        net_name = net["name"]
        resolved_pins = []

        for pr in net["pins"]:
            ref = pr["ref"]
            pin_id = pr["pin"]

            if ref not in component_pin_data:
                logger.warning(f"Unknown component {ref}")
                continue

            pins = component_pin_data[ref]
            pin_data = find_pin(pin_id, pins)
            if pin_data is None:
                logger.warning(
                    f"Could not find pin {pin_id} on {ref}. "
                    f"Available: {[(n, d['name']) for n, d in pins.items()]}"
                )
                continue

            # Get component position
            comp = next(c for c in components if c["ref"] == ref)
            cx = comp.get("x", 50.8)
            cy = comp.get("y", 50.8)
            crot = comp.get("rotation", 0)

            abs_x, abs_y, pin_angle = get_pin_position(pin_data, cx, cy, crot)
            resolved_pins.append((abs_x, abs_y, pin_angle))

        if not resolved_pins:
            continue

        # Place labels at each pin
        for abs_x, abs_y, pin_angle in resolved_pins:
            # Calculate label offset (away from body)
            if 45 <= pin_angle < 135:
                dx, dy, label_rot = 0.0, -LABEL_OFFSET, 90
            elif 135 <= pin_angle < 225:
                dx, dy, label_rot = LABEL_OFFSET, 0.0, 0
            elif 225 <= pin_angle < 315:
                dx, dy, label_rot = 0.0, LABEL_OFFSET, 270
            else:
                dx, dy, label_rot = -LABEL_OFFSET, 0.0, 180

            label_x = round(abs_x + dx, 2)
            label_y = round(abs_y + dy, 2)

            key = (net_name, label_x, label_y)
            if key not in placed_labels:
                labels.append(make_label(net_name, label_x, label_y, label_rot))
                # Wire from pin to label
                wires.append(make_wire(abs_x, abs_y, label_x, label_y))
                placed_labels.add(key)

    # Phase 5: Assemble the schematic file
    sch_uuid = str(uuid.uuid4())

    # Build the lib_symbols section by removing ONLY the last ')' (lib_symbols closer)
    # then appending dynamic symbols, then re-adding the closer.
    seed_text = seed_lib_symbols.rstrip()
    # Remove exactly ONE closing paren (the lib_symbols section closer)
    if seed_text.endswith(")"):
        seed_text = seed_text[:-1]

    dynamic_blocks = []
    for lib_id, block in lib_symbols_needed.items():
        if block is not None:
            indented = "\n".join(
                ("    " + line if line.strip() else line)
                for line in block.split("\n")
            )
            dynamic_blocks.append(indented)

    all_lib_symbols = seed_text + "\n" + "\n".join(dynamic_blocks) + "\n  )"

    # Build full schematic
    schematic = f"""(kicad_sch (version 20250114) (generator "eeschema") (uuid "{sch_uuid}") (paper "A3")
  {all_lib_symbols}
  {''.join(chr(10) + inst for inst in instances)}
  {''.join(chr(10) + w for w in wires)}
  {''.join(chr(10) + l for l in labels)}
  (sheet_instances
    (path "/" (page "1"))
  )
)
"""
    output.write_text(schematic, encoding="utf-8")
    logger.info(f"Wrote schematic: {output}")


def main():
    circuit_path = sys.argv[1] if len(sys.argv) > 1 else "arduino_nano_circuit.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "arduino_nano.kicad_sch"

    circuit = load_circuit(circuit_path)
    logger.info(f"Loaded circuit: {len(circuit['components'])} components, {len(circuit['nets'])} nets")

    generate_arduino_nano(circuit, output_path)

    # Validate with ERC
    try:
        from engine.backends.validation import validate_schematic
        result = validate_schematic(Path(output_path))
        logger.info(f"ERC: {result['erc']['errors']} errors, {result['erc']['warnings']} warnings")
    except Exception as e:
        logger.warning(f"ERC validation failed: {e}")

    # Create project file
    import json
    pro_path = Path(output_path).with_suffix(".kicad_pro")
    if not pro_path.exists():
        pro_data = {
            "board": {"design_settings": {"defaults": {}, "diff_pair_dimensions": [], "drc_exclusions": [], "rules": {}, "track_widths": [], "via_dimensions": []}, "filename": pro_path.with_suffix(".kicad_pcb").name},
            "boards": [],
            "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
            "meta": {"filename": pro_path.name, "version": 1},
            "net_settings": {"classes": [], "meta": {"version": 0}},
            "pcbnew": {"page_layout_descr_file": ""},
            "sheets": [["root", Path(output_path).name]],
            "text_variables": {},
        }
        pro_path.write_text(json.dumps(pro_data, indent=2) + "\n")
        logger.info(f"Created project file: {pro_path}")


if __name__ == "__main__":
    main()
