---
name: schematic_gen
description: Explicit alternative; prefer kicad-schematic-generation.
---

## BoltzPCB workflow integration

This is an alternative implementation. Prefer the task-specific BoltzPCB skills
listed in `eda-design-workflow`. Use this alternative only for an explicit user
choice or a demonstrated capability gap, explain the switch, and retain the
project evidence, checks and recorded user skips. Never use it to bypass a
failed prerequisite or silently replace the preferred pipeline.

# Schematic Generation Skill

This standalone generator is available when the user explicitly asks for `/schematic_gen`. For ordinary requests to create an editable native KiCad schematic from a circuit description, use `kicad-schematic-generation` and its `kicad_generation` plugin.

## When to Use

- User explicitly invokes `/schematic_gen` or names this standalone generator.
- A general request to create a circuit or native KiCad schematic routes to `kicad-schematic-generation`.

## Workflow

### Step 1: Analyze the Request

From the user's natural language description, identify:
- What circuit to build (e.g., LED indicator, voltage divider, RC filter)
- Input voltage / power source
- Required components and their values
- How components connect to each other

### Step 2: Generate the Circuit Definition

Create a JSON circuit definition with this exact schema:

```json
{
  "components": [
    {
      "ref": "R1",
      "type": "resistor",
      "value": "10k",
      "footprint": "Resistor_SMD:R_0805_2012Metric",
      "x": 50.8,
      "y": 50.8,
      "rotation": 0
    }
  ],
  "nets": [
    {
      "name": "VCC",
      "pins": [{"ref": "R1", "pin": "1"}]
    },
    {
      "name": "OUT",
      "pins": [{"ref": "R1", "pin": "2"}]
    }
  ]
}
```

**Component fields:**
- `ref` (required): Reference designator (R1, C1, D1, U1, etc.)
- `type` (required): Component type — see supported types below
- `value` (optional): Component value (10k, 100nF, RED, etc.)
- `footprint` (optional): KiCAD footprint string
- `x`, `y` (optional): Position in mm (auto-layout if omitted)
- `rotation` (optional): Rotation in degrees (default 0)

**Supported component types:**
| Type string | KiCAD Symbol | Pins |
|---|---|---|
| resistor, R | Device:R | 1, 2 |
| capacitor, C | Device:C | 1, 2 |
| inductor, L | Device:L | 1, 2 |
| crystal | Device:Crystal | 1, 2 |
| diode, D | Device:D | K(1), A(2) |
| led, LED | Device:LED | K(1), A(2) |
| transistor_npn, NPN | Device:Q_NPN | B(1), C(2), E(3) |
| transistor_nmos, NMOS | Device:Q_NMOS | G(1), S(2), D(3) |
| opamp, lm358 | Amplifier_Operational:LM358 | in-(1), in+(2), out(3), V-(4), V+(8) |
| connector_01x02 | Connector_Generic:Conn_01x02 | Pin_1(1), Pin_2(2) |
| connector_01x04 | Connector_Generic:Conn_01x04 | Pin_1-Pin_4 |
| regulator, ams1117 | Regulator_Linear:AMS1117-3.3 | GND(1), VO(2), VI(3) |
| switch, button | Switch:SW_Push | 1, 2 |

For any other symbol use `"Library:SymbolName"` format as the type — it will be dynamically loaded from KiCAD's installed libraries. Before generation, confirm the exact symbol name in the target KiCAD library; familiar part names and package/variant suffixes are not interchangeable, and a nonexistent library symbol stops generation.

**Pin identification:**
- Use pin numbers (`"1"`, `"2"`) for passives (R, C, L, Crystal, Switch)
- Use pin names (`"A"`, `"K"`) for LEDs and diodes
- Use pin names (`"B"`, `"C"`, `"E"`) for transistors
- Use pin names (`"V+"`, `"V-"`, `"out"`, etc.) for ICs

**Position guidelines:**
- Default grid spacing is 12.7mm between components
- Leave at least 25mm between components for routing space
- Place components left-to-right following signal flow
- If omitting positions, auto-layout will arrange in a grid

### Step 3: Locate and Run the Generator

Use the installed skill's actual directory rather than a remembered machine-specific project path. Locate `generate.py` with `search_files` under the active profile's skill directory, then invoke that file from its containing directory. Check `--help` first. If imports report a missing declared dependency, use `uv run --with sexpdata --with kicad-skip python <absolute-path-to-generate.py> ...` so the environment remains reproducible without changing the system interpreter. Do not patch an installed generator just to get one schematic generated; if generation cannot be completed, give a clearly labeled text schematic instead of claiming a file exists.

Execute the generation script:

```bash
cd <actual-generator-directory> && python3 -c "
import json, sys
sys.path.insert(0, '.')
from generate import generate_schematic

circuit = <YOUR_CIRCUIT_JSON_HERE>

generate_schematic(circuit, '<OUTPUT_PATH>.kicad_sch')
"
```

Or write the circuit JSON to a file and run:

```bash
cd /home/boltzmann6/chip_design/chip_design/schematic_generation/schematic_gen && python3 generate.py circuit.json output.kicad_sch
```

### Step 4: Validate with ERC

After generation, the engine automatically runs ERC (Electrical Rules Check) via `kicad-cli sch erc`. To run manually:

```bash
kicad-cli sch erc --format report --output output.erc.rpt output.kicad_sch
```

Or use the Python validation module:

```bash
cd /home/boltzmann6/chip_design/chip_design/schematic_generation/schematic_gen && python3 -c "
import sys; sys.path.insert(0, '.')
from engine.backends.validation import validate_schematic
result = validate_schematic('output.kicad_sch')
print(f'Passed: {result[\"passed\"]}')
print(f'Errors: {result[\"erc\"][\"errors\"]}')
print(f'Warnings: {result[\"erc\"][\"warnings\"]}')
"
```

**ERC result interpretation:**
- Do not equate generator-reported nets with KiCad electrical connectivity; they may only reflect the input JSON.
- Treat every ERC error as a failed validation. Inspect the report and correct the schematic, then rerun ERC; never dismiss dangling labels, unconnected pins, or undriven power pins as routine standalone-circuit warnings.
- Classify warnings individually and state any intentionally accepted warnings with their reason.
- Before delivery, verify the actual generated schematic via ERC and export its netlist to confirm intended component-pin connectivity; ERC alone does not prove that the JSON values made it into the output or that physical symbol pins map as intended.
- Compare the generated schematic’s component references and values against the approved circuit definition before delivery. If a pre-existing input file conflicts with the agreed values, inspect and reconcile it rather than silently accepting stale values.
- If generation exits nonzero, do not treat an output file that happens to exist as a successful artifact: inspect its completeness, repair the cause or use a validated alternate route, and rerun generation before reporting it.

### Step 5: (Optional) DRC Validation for PCBs

If a `.kicad_pcb` file is generated from the schematic, run DRC:

```bash
cd /home/boltzmann6/chip_design/chip_design/schematic_generation/schematic_gen && python3 -c "
import sys; sys.path.insert(0, '.')
from engine.backends.validation import validate_pcb
result = validate_pcb('output.kicad_pcb')
print(f'Passed: {result[\"passed\"]}')
print(f'Violations: {result[\"drc\"][\"summary\"][\"total\"]}')
"
```

### Step 6: Report Results

Only report a generated artifact after the generator exits successfully and the output file exists. Check its symbols and wiring, then run ERC; distinguish a generated file from one whose ERC passed. For a simple educational circuit, a compact readable text schematic is an acceptable fallback, but label it as text rather than a KiCad artifact.

Tell the user:
- The output file path
- Number of components and nets generated
- ERC result: errors and warnings count
- Any warnings (orphaned wires, missing symbols, etc.)

## Common Circuit Patterns

### LED Indicator
```json
{
  "components": [
    {"ref": "R1", "type": "R", "value": "330", "x": 50.8, "y": 50.8},
    {"ref": "D1", "type": "LED", "value": "RED", "x": 76.2, "y": 50.8}
  ],
  "nets": [
    {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
    {"name": "NET_R1_D1", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "D1", "pin": "A"}]},
    {"name": "GND", "pins": [{"ref": "D1", "pin": "K"}]}
  ]
}
```

### Voltage Divider
```json
{
  "components": [
    {"ref": "R1", "type": "R", "value": "10k", "x": 50.8, "y": 50.8},
    {"ref": "R2", "type": "R", "value": "10k", "x": 76.2, "y": 50.8}
  ],
  "nets": [
    {"name": "VIN", "pins": [{"ref": "R1", "pin": "1"}]},
    {"name": "VOUT", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
    {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]}
  ]
}
```

### RC Low-Pass Filter
```json
{
  "components": [
    {"ref": "R1", "type": "R", "value": "1k", "x": 50.8, "y": 50.8},
    {"ref": "C1", "type": "C", "value": "100nF", "x": 76.2, "y": 63.5}
  ],
  "nets": [
    {"name": "VIN", "pins": [{"ref": "R1", "pin": "1"}]},
    {"name": "VOUT", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "C1", "pin": "1"}]},
    {"name": "GND", "pins": [{"ref": "C1", "pin": "2"}]}
  ]
}
```

### Decoupling Capacitor
```json
{
  "components": [
    {"ref": "C1", "type": "C", "value": "100nF", "x": 50.8, "y": 50.8}
  ],
  "nets": [
    {"name": "VCC", "pins": [{"ref": "C1", "pin": "1"}]},
    {"name": "GND", "pins": [{"ref": "C1", "pin": "2"}]}
  ]
}
```

## Implementation Files

| File | Purpose |
|---|---|
| `generate.py` | CLI and Python API entry point |
| `engine/schematic_engine.py` | Core engine: template loading, cloning, wiring, ERC validation, output |
| `engine/circuit_parser.py` | Circuit JSON validation, type mapping, auto-layout |
| `engine/backends/validation.py` | ERC (schematic) and DRC (PCB) validation via kicad-cli |
| `engine/backends/design_rules.py` | PCB design rule configuration and DRC runner |
| `engine/backends/dynamic_symbol_loader.py` | Load symbols from KiCAD system libraries |
| `engine/backends/pin_locator.py` | Absolute pin position computation |
| `engine/backends/wire_manager.py` | Wire creation and orthogonal routing |
| `engine/backends/schematic_snap.py` | Snap coordinates to KiCAD grid |
| `engine/backends/schematic_analysis.py` | Orphaned wire and overlap detection |
| `templates/seed_template.kicad_sch` | Template with 13 seed symbols for cloning |
| `templates/build_template.py` | Script to rebuild the template from KiCAD libraries |

## Dependencies

- `kicad-skip` (pip): Reads/writes .kicad_sch files
- `sexpdata` (pip): S-expression parser
- KiCAD system libraries at `/usr/share/kicad/symbols/` for dynamic loading
- `kicad-cli` for ERC/DRC validation (bundled with KiCAD 8.0+)

## Limitations

- Components must exist either in the seed template or in KiCAD's installed libraries
- Auto-layout is simple grid placement — complex circuits may need manual positioning
- No hierarchical sheet support yet
- Power symbols (VCC/GND) are global labels, not power ports — KiCAD may prompt to connect them
