---
name: kicad-gen
description: Explicit alternative; prefer kicad-library-generator.
  Generate KiCad v9 symbol (.kicad_sym) and footprint (.kicad_mod inside .pretty) files
  for electronic components using ComponentFactory backend. Triggers on: "create a symbol for X",
  "create footprint for Y", "generate kicad symbol", "generate kicad footprint", "make a symbol",
  "make a footprint", "kicad symbol for", "kicad footprint for", or any request involving KiCad
  symbol or footprint generation for a component.
---

## BoltzPCB workflow integration

This is an alternative implementation. Prefer the task-specific BoltzPCB skills
listed in `eda-design-workflow`. Use this alternative only for an explicit user
choice or a demonstrated capability gap, explain the switch, and retain the
project evidence, checks and recorded user skips. Never use it to bypass a
failed prerequisite or silently replace the preferred pipeline.

# KiCad Symbol & Footprint Generator

Generates KiCad v9 S-expression symbol and footprint files using the backend
`ComponentFactory` class from `create_and_validate.py`. Takes a component description,
researches pin/pad details, writes and runs a Python script, and validates the output.

---

## When To Use This Skill

- User asks to create a KiCad symbol for a specific component (e.g., "create a symbol for STM32F407")
- User asks to create a KiCad footprint for a specific package (e.g., "create footprint for QFP-48")
- User asks to generate both symbol and footprint together
- User provides component specs (pin list, package type) and wants KiCad files generated

## Base Paths

```
PROJECT_ROOT = /home/boltzmann6/chip_design/chip_design/schematic_generation/symbol_footprint
BACKEND      = <PROJECT_ROOT>/create_and_validate.py   (ComponentFactory class)
SYMBOLS_OUT  = <PROJECT_ROOT>/output/symbols/           (.kicad_sym files)
FOOTPRINTS_OUT = <PROJECT_ROOT>/output/footprints/       (.pretty dirs with .kicad_mod files)
MCP_SERVER   = <PROJECT_ROOT>/../kicad-mcp-server/.claude/KiCAD-MCP-Server/python
```

## Step-by-Step Instructions

### 1. Gather Component Information

Determine what the user needs:

- **Component name** (e.g., STM32F407, LM358, TMC2209)
- **What to generate**: symbol only, footprint only, or both
- **Package type** (e.g., QFP-48, SOIC-8, DIP-8, SOT-23, 0603)
- **Pin/pad details**: names, numbers, types, positions

If the user provides a part name but not full details, research the component
(datasheet, pin functions, package dimensions).

### 2. Map Pin/Pad Types

**Symbol pin types** (use these exact strings):
`input`, `output`, `bidirectional`, `tri_state`, `passive`, `free`, `unspecified`,
`power_in`, `power_out`, `open_collector`, `open_emitter`, `no_connect`

**Symbol pin shapes** (default: `line`):
`line`, `inverted`, `clock`, `inverted_clock`, `input_low`, `clock_low`,
`output_low`, `falling_edge_clock`, `non_logic`

**Footprint pad types**: `smd`, `thru_hole`, `np_thru_hole`
**Footprint pad shapes**: `rect`, `circle`, `oval`, `roundrect`

### 3. Write the Python Script

Use this template. Save the script into `PROJECT_ROOT/` and run it from there.

```python
import sys
from pathlib import Path
from unittest.mock import MagicMock

if "pcbnew" not in sys.modules:
    _stub = MagicMock(name="pcbnew")
    _stub.__file__ = "/fake/pcbnew.so"
    _stub.GetBuildVersion.return_value = "9.0.0-stub"
    sys.modules["pcbnew"] = _stub

BASE = Path("/home/boltzmann6/chip_design/chip_design/schematic_generation/symbol_footprint")
sys.path.insert(0, str(BASE))

from create_and_validate import ComponentFactory

factory = ComponentFactory(output_dir=BASE / "output")
```

**For symbols** call `factory.create_symbol()`:

```python
result = factory.create_symbol(
    name="ComponentName",
    reference_prefix="U",        # U=IC, R=resistor, C=cap, etc.
    description="Human-readable description",
    keywords="searchable tags",
    datasheet="https://url.to/datasheet.pdf",
    footprint="Library:FootprintName",
    library_path=str(BASE / "output" / "symbols" / "category.kicad_sym"),
    pins=[
        {"name": "VCC", "number": "1", "type": "power_in",
         "at": {"x": -5.08, "y": 10.16, "angle": 0}, "length": 2.54},
        {"name": "GND", "number": "2", "type": "power_in",
         "at": {"x": -5.08, "y": -10.16, "angle": 0}, "length": 2.54},
        {"name": "OUT", "number": "3", "type": "output",
         "at": {"x": 5.08, "y": 0, "angle": 180}, "length": 2.54},
    ],
    rectangles=[{"x1": -2.54, "y1": -3.81, "x2": 2.54, "y2": 3.81}],
)
```

**For footprints** call `factory.create_footprint()`:

```python
result = factory.create_footprint(
    name="Package_Name",
    description="Package description",
    tags="package smd 0603",
    library_path=str(BASE / "output" / "footprints" / "category.pretty"),
    pads=[
        {"number": "1", "type": "smd", "shape": "rect",
         "at": {"x": -0.75, "y": 0}, "size": {"w": 0.8, "h": 0.9}},
        {"number": "2", "type": "smd", "shape": "rect",
         "at": {"x": 0.75, "y": 0}, "size": {"w": 0.8, "h": 0.9}},
    ],
    courtyard={"x1": -1.1, "y1": -0.65, "x2": 1.1, "y2": 0.65},
    silkscreen={"x1": -0.85, "y1": -0.55, "x2": 0.85, "y2": 0.55},
    fab_layer={"x1": -0.8, "y1": -0.4, "x2": 0.8, "y2": 0.4},
)
```

**For through-hole pads** include `drill`:
```python
{"number": "1", "type": "thru_hole", "shape": "circle",
 "at": {"x": -3.81, "y": 0}, "size": {"w": 1.6, "h": 1.6}, "drill": 0.8}
```

### 4. Run the Script

```bash
cd /home/boltzmann6/chip_design/chip_design/schematic_generation/symbol_footprint && python <script_name>.py
```

### 5. Report Results

Check the result dict:
- `result["success"]` — overall pass/fail (creation + validation)
- `result["output_path"]` — where the file was written
- `result["validation"]` — detailed check results with pass/fail per check

Report to the user what was generated, where, and any validation failures.

## Output File Structure

```
output/
  symbols/
    analog.kicad_sym              # symbol library (multiple symbols per file)
    passives.kicad_sym
    microcontrollers.kicad_sym
  footprints/
    passives.pretty/              # footprint library (directory)
      R_0603.kicad_mod           # individual footprint
      C_0603.kicad_mod
    ic_packages.pretty/
      DIP-8.kicad_mod
      QFP-48.kicad_mod
```

## Rules & Guardrails

- ALWAYS use `ComponentFactory` from `create_and_validate.py` — it handles creation AND validation
- ALWAYS include `reference_prefix` for symbols — this sets the Reference property (e.g., "U"). KiCad displays it as "U?" when placed; numbering (U1, U2, etc.) is done via **Tools → Annotate Schematic** in the KiCad schematic editor. This is standard KiCad behavior, not a bug.
- ALWAYS include a `rectangles` or `polylines` body graphic for IC symbols
- ALWAYS include `courtyard` for footprints (required for DRC)
- ALWAYS use string pad/pin numbers (e.g., `"1"` not `1`)
- ALWAYS use KiCad v9 coordinate conventions: mm for footprints, 2.54mm grid for symbols
- ALWAYS prevent label overlap by following these spacing rules:
  - For symbols with 8 or fewer pins per side: use 2.54mm vertical spacing, pin length 2.54mm, pin x-offset ±5.08mm
  - For symbols with 9-16 pins per side: use 5.08mm vertical spacing, pin length 5.08mm, pin x-offset ±10.16mm, widen body to ±5.08mm
  - For symbols with 17+ pins per side: consider splitting into multiple logical sections (power, GPIO, comms) or use 5.08mm spacing with a larger body
  - Keep pin names short (max 6 chars for 2.54mm spacing, max 8 chars for 5.08mm spacing). Use alternate function as the name only when it's the primary function (e.g., "MOSI" not "PB3/MOSI")
- Pin `at` dict must include `angle`: 0=left, 180=right, 90=down(symbol), 270=up(symbol)
- Footprint pad `size` is `{"w": width, "h": height}` in mm
- NEVER skip validation — report failures if `result["success"]` is False
- NEVER guess pin/pad counts for complex ICs — verify from datasheet

## Reference Designator Prefixes

| Prefix | Component Type |
|--------|---------------|
| U      | ICs, op-amps, microcontrollers |
| R      | Resistors |
| C      | Capacitors |
| L      | Inductors |
| D      | Diodes, LEDs |
| Q      | Transistors, FETs |
| J      | Connectors, jacks |
| Y      | Crystals, oscillators |
| SW     | Switches |
| FB     | Ferrite beads |
| TP     | Test points |
| T      | Transformers |
| K      | Relays |

## Example

User: "create a symbol and footprint for a TLV75901 LDO regulator"

```python
import sys
from pathlib import Path
from unittest.mock import MagicMock

if "pcbnew" not in sys.modules:
    _stub = MagicMock(name="pcbnew")
    _stub.__file__ = "/fake/pcbnew.so"
    _stub.GetBuildVersion.return_value = "9.0.0-stub"
    sys.modules["pcbnew"] = _stub

BASE = Path("/home/boltzmann6/chip_design/chip_design/schematic_generation/symbol_footprint")
sys.path.insert(0, str(BASE))

from create_and_validate import ComponentFactory

factory = ComponentFactory(output_dir=BASE / "output")

# Symbol
sym = factory.create_symbol(
    name="TLV75901",
    reference_prefix="U",
    description="TLV75901 1A low-dropout linear regulator, adjustable",
    keywords="ldo regulator tlv75901 ti low-dropout",
    datasheet="https://www.ti.com/lit/ds/symlink/tlv75901p.pdf",
    library_path=str(BASE / "output" / "symbols" / "voltage_regulators.kicad_sym"),
    pins=[
        {"name": "IN",   "number": "1", "type": "power_in",  "at": {"x": -5.08, "y": 5.08, "angle": 0},   "length": 2.54},
        {"name": "GND",  "number": "2", "type": "power_in",  "at": {"x": -5.08, "y": -5.08, "angle": 0},  "length": 2.54},
        {"name": "EN",   "number": "3", "type": "input",     "at": {"x": -5.08, "y": 0, "angle": 0},      "length": 2.54},
        {"name": "FB",   "number": "4", "type": "input",     "at": {"x": 5.08, "y": -5.08, "angle": 180},  "length": 2.54},
        {"name": "OUT",  "number": "5", "type": "power_out", "at": {"x": 5.08, "y": 5.08, "angle": 180},   "length": 2.54},
    ],
    rectangles=[{"x1": -3.81, "y1": -6.35, "x2": 3.81, "y2": 6.35}],
)

# Footprint (SOT-23-5)
fp = factory.create_footprint(
    name="SOT-23-5_TLV75901",
    description="SOT-23-5 footprint for TLV75901 LDO",
    tags="sot23-5 ldo regulator smd",
    library_path=str(BASE / "output" / "footprints" / "voltage_regulators.pretty"),
    pads=[
        {"number": "1", "type": "smd", "shape": "rect", "at": {"x": -0.95, "y": -0.925}, "size": {"w": 0.7, "h": 0.95}},
        {"number": "2", "type": "smd", "shape": "rect", "at": {"x": -0.95, "y": 0.925},  "size": {"w": 0.7, "h": 0.95}},
        {"number": "3", "type": "smd", "shape": "rect", "at": {"x": -0.95, "y": 0},      "size": {"w": 0.7, "h": 0.95}},
        {"number": "4", "type": "smd", "shape": "rect", "at": {"x": 0.95, "y": 0.925},   "size": {"w": 0.7, "h": 0.95}},
        {"number": "5", "type": "smd", "shape": "rect", "at": {"x": 0.95, "y": -0.925},  "size": {"w": 0.7, "h": 0.95}},
    ],
    courtyard={"x1": -1.5, "y1": -1.6, "x2": 1.5, "y2": 1.6},
    silkscreen={"x1": -1.1, "y1": -1.3, "x2": 1.1, "y2": 1.3},
    fab_layer={"x1": -0.9, "y1": -1.1, "x2": 0.9, "y2": 1.1},
)

print(f"Symbol: {'PASS' if sym['success'] else 'FAIL'} -> {sym['output_path']}")
print(f"Footprint: {'PASS' if fp['success'] else 'FAIL'} -> {fp['output_path']}")
```
