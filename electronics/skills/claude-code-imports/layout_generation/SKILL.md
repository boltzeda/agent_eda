---
name: layout_generation
description: Explicit alternative; prefer pcb-workflow.
  Generate PCB layouts (.kicad_pcb) from circuit netlists using the standalone pcbnew-based
  layout generator. Triggers on: "generate a PCB layout", "create PCB layout", "layout a circuit",
  "place components on a PCB", "auto-route PCB", "generate kicad_pcb", "PCB layout from netlist",
  "convert netlist to PCB", "board layout", "layout generation", "/layout_generation".
  Also use when a user provides a circuit description and wants a physical PCB layout file.
---

## BoltzPCB workflow integration

This is an alternative implementation. Prefer the task-specific BoltzPCB skills
listed in `eda-design-workflow`. Use this alternative only for an explicit user
choice or a demonstrated capability gap, explain the switch, and retain the
project evidence, checks and recorded user skips. Never use it to bypass a
failed prerequisite or silently replace the preferred pipeline.

# PCB Layout Generation

Generate PCB layouts from circuit netlists. When the native `pcb-workflow`
plugin is available, use that staged workflow instead of this standalone
pipeline; it requires preprocessing review, confirmed board constraints, a
placement-only checkpoint, and explicit approval before routing. Use the
standalone `layout_gen` pipeline only when the plugin workflow is unavailable
or the user specifically requests it.

---

## When To Use This Skill

- User asks to generate a PCB layout from a circuit description or netlist
- User provides component + net information and wants a `.kicad_pcb` file
- User asks for component placement, board outline, or copper pour generation
- User says "layout_generation", "/layout_generation", or "generate PCB layout"
- User has a schematic and wants to produce a board file
- User provides a KiCad XML netlist (`.xml`) and wants a PCB layout

Do NOT use when:
- User only needs a schematic (use `kicad-schematic-generation` skill)
- User asks to create symbols or footprints (use `kicad-gen` skill)
- Task is purely theoretical with no file output needed

---

## Base Paths

```
LAYOUT_GEN = <discovered-layout-generator-root>
PIPELINE   = LAYOUT_GEN/layout_gen/pipeline.py
MODELS     = LAYOUT_GEN/layout_gen/models.py
```

---

## Step-by-Step Instructions

### Step 1: Gather Circuit Information

From the user's description, extract:

1. **Compone

  Controlled via BoardConfig dataclass — all fields have defaults:
nts** — each needs:
   - `reference` (required): e.g., "R1", "U3", "J1"
   - `footprint` (required): KiCad footprint ID, e.g., "Resistor_SMD:R_0402_1005Metric"
   - `value` (optional): e.g., "10k", "100nF", "STM32F407"

2. **Nets** — each needs:
   - `name` (required): e.g., "GND", "SDA", "VCC"
   - `connections` (required): list of `{component, pin}` pairs

3. **Board config** (optional — all have sensible defaults):
   - Board dimensions (auto-calculated if not specified)
   - Number of copper layers (default: 2)
   - Design rules: clearance, track width, via size

If the user provides a KiCad XML netlist file, use it directly via `netlist_xml_path=`.
If the user provides a `.kicad_sch` schematic, use `netlist_from_schematic()`.

### Step 2: Determine Board Configuration

Use these defaults unless the user specifies otherwise:

| Parameter | Default | When to override |
|-----------|---------|-----------------|
| `auto_size` | True | Set False + explicit dimensions for constrained designs |
| `copper_layers` | 2 | Use 4 for BGA, high-speed, or dense designs |
| `clearance_mm` | 0.15 | Use 0.6+ for >30V, 1.25+ for >50V (IPC-2221) |
| `track_width_mm` | 0.25 | Use 0.5+ for >1A, 1.0+ for >3A power traces |
| `via_diameter_mm` | 0.8 | Use 1.0+ for power vias |
| `via_drill_mm` | 0.4 | Match via_diameter at ~50% ratio |
| `margin_mm` | 2.5 | Increase for boards with edge connectors |
| `ground_pours` | True | Disable for simple single-sided designs |
| `autoroute` | False | Enable only if Freerouting JAR + Java 21+ available |

### Step 3: Write and Execute the Layout Script

Create a Python script and run it from the layout-generator directory:

```bash
cd <discovered-layout-generator-root> && python3 -c "
from layout_gen import LayoutPipeline, BoardConfig

result = LayoutPipeline(board_config=BoardConfig(
    # ... user-specified config ...
)).run(
    netlist_data={
        # ... netlist from step 1 ...
    },
    output_path='<OUTPUT_PATH>.kicad_pcb',
    autoroute=False,
    ground_pours=True,
    ground_net='GND',
)

print(result)
"
```

**For KiCad XML netlist input:**
```python
result = LayoutPipeline().run(
    netlist_xml_path='/path/to/netlist.xml',
    output_path='output.kicad_pcb',
)
```

**For schematic input:**
```python
from layout_gen.netlist_parser import netlist_from_schematic
from layout_gen import LayoutPipeline

netlist = netlist_from_schematic('/path/to/schematic.kicad_sch')
pipeline = LayoutPipeline()
pipeline.load_netlist(netlist_data=None, netlist_xml_path=None)
pipeline.netlist = netlist
# ... continue with remaining steps
```

### Step 4: Validate and Report

Check the `result` dict:

```python
if result["success"]:
    # result contains:
    # - output_path: str
    # - board_width_mm, board_height_mm: float
    # - components_placed: int
    # - nets: int
else:
    # result contains:
    # - failed_step: str
    # - error: str
```

Report to the user:
- Output file path
- Board dimensions (auto-calculated or specified)
- Number of components placed
- Number of nets
- Any warnings (unresolved footprints, skipped routing)

---

## Netlist Format Reference

### Structured Dict Format

```python
netlist_data = {
    "components": [
        {
            "reference": "R1",                                     # Required
            "value": "10k",                                        # Optional
            "footprint": "Resistor_SMD:R_0402_1005Metric",        # Required
        },
        {
            "reference": "U1",
            "value": "STM32F407",
            "footprint": "Package_QFP:LQFP-100_14x14mm_P0.5mm",
        },
    ],
    "nets": [
        {
            "name": "VCC",                                         # Required
            "connections": [                                        # Required
                {"component": "R1", "pin": "1"},
                {"component": "U1", "pin": "VCC"},
            ],
        },
        {
            "name": "GND",
            "connections": [
                {"component": "R1", "pin": "2"},
                {"component": "U1", "pin": "GND"},
            ],
        },
    ],
}
```

### KiCad XML Netlist

Standard KiCad XML netlist format exported by:
- KiCad GUI: Tools → Generate Netlist File
- CLI: `kicad-cli sch export netlist --format kicadxml --output out.xml schematic.kicad_sch`

---

## Advanced Usage: Step-by-Step Pipeline

For more control over individual stages:

```python
from layout_gen import LayoutPipeline, BoardConfig

pipeline = LayoutPipeline(board_config=BoardConfig(auto_size=True))

# Each step can be run individually
pipeline.load_netlist(netlist_data=netlist_data)
pipeline.resolve_footprints()
pipeline.build_connectivity()
pipeline.compute_placement()
pipeline.build_board()
pipeline.sync_nets()
# Optional: pipeline.route(output_path)
pipeline.add_ground_planes(net_name="GND")
pipeline.save("output.kicad_pcb")
```

### Manual Routing (without Freerouting)

```python
from layout_gen.router import ManualRouter

router = ManualRouter(pipeline.board)

# Route individual traces
router.route_trace((10.0, 5.0), (20.0, 5.0), layer="F.Cu", width_mm=0.3, net_name="SDA")

# Route pad-to-pad with automatic via insertion
router.route_pad_to_pad("R1", "1", "U1", "SDA", layer="F.Cu", width_mm=0.25)

# Add individual vias
router.add_via((15.0, 10.0), net_name="GND", from_layer="F.Cu", to_layer="B.Cu")
```

### Copper Pour Customization

```python
from layout_gen.copper_pours import add_ground_pours, add_power_pour

# Ground on both outer layers
add_ground_pours(
    pipeline.board,
    board_width_mm=50.0, board_height_mm=30.0,
    net_name="GND", layers=["F.Cu", "B.Cu"],
    clearance_mm=0.2,
)

# Dedicated power plane on inner layer
add_power_pour(
    pipeline.board, layer="In1.Cu", net_name="3V3",
    board_width_mm=50.0, board_height_mm=30.0,
)
```

---

## Circuit Classification Guide

Use these heuristics to determine board configuration from circuit type:

### RF/Microwave (>100 MHz)
- `copper_layers=4` (minimum), solid ground planes
- `clearance_mm=0.2`, `track_width_mm` matched to 50Ω impedance
- Minimize trace lengths, via stitch ground planes

### Analog/Precision
- `clearance_mm=0.5` for sensitive signals
- Guard traces, star grounding
- Separate analog/digital ground planes

### Digital/High-Speed
- `copper_layers=4` for impedance control
- Length-matched buses, decoupling cap arrays
- `track_width_mm` per impedance calculation

### Power Electronics (>1A or >50V)
- `track_width_mm`: use IPC-2221 current table (1A ≈ 0.25mm ext, 3A ≈ 1.0mm)
- `clearance_mm`: 0.6mm for 30-50V, 1.25mm for >50V
- `via_diameter_mm=1.0+` for power vias, multiple parallel vias

### Mixed-Signal
- `copper_layers=4` (split ground/power planes)
- Physical separation between analog and digital sections
- Single-point ground connection between domains

---

## Rules & Guardrails

- ALWAYS provide at minimum: component reference, footprint ID, and net connections
- ALWAYS use valid KiCad footprint IDs in "Library:FootprintName" format (e.g., "Resistor_SMD:R_0402_1005Metric")
- ALWAYS verify footprint IDs exist in the KiCad library — invalid IDs will produce warnings but the pipeline will continue
- ALWAYS set `autoroute=False` unless Freerouting JAR is confirmed available at `~/.kicad-mcp/freerouting.jar` with Java 21+
- NEVER guess pin numbers for ICs — verify from datasheet or symbol
- NEVER mutate BoardConfig objects — create new ones with different parameters
- Confirm mechanical, board-size, and fabrication constraints when they affect
  placement or manufacture; for the native `pcb-workflow` path, follow its
  preprocessing review and board-parameter confirmation gate.
- Never start routing until the user explicitly approves the exact reviewed
  placement. Use only the router required by the selected workflow.

---

## Common Circuit Patterns

### Voltage Divider
```python
netlist_data = {
    "components": [
        {"reference": "R1", "value": "10k", "footprint": "Resistor_SMD:R_0402_1005Metric"},
        {"reference": "R2", "value": "10k", "footprint": "Resistor_SMD:R_0402_1005Metric"},
    ],
    "nets": [
        {"name": "VCC", "connections": [{"component": "R1", "pin": "1"}]},
        {"name": "VOUT", "connections": [{"component": "R1", "pin": "2"}, {"component": "R2", "pin": "1"}]},
        {"name": "GND", "connections": [{"component": "R2", "pin": "2"}]},
    ],
}
```

### LED with Resistor
```python
netlist_data = {
    "components": [
        {"reference": "R1", "value": "330", "footprint": "Resistor_SMD:R_0805_2012Metric"},
        {"reference": "D1", "value": "Red LED", "footprint": "LED_SMD:LED_0805_2012Metric"},
    ],
    "nets": [
        {"name": "VCC", "connections": [{"component": "R1", "pin": "1"}]},
        {"name": "NET_R1_D1", "connections": [{"component": "R1", "pin": "2"}, {"component": "D1", "pin": "1"}]},
        {"name": "GND", "connections": [{"component": "D1", "pin": "2"}]},
    ],
}
```

### LDO Power Supply
```python
netlist_data = {
    "components": [
        {"reference": "U1", "value": "TLV75901", "footprint": "Package_TO_SOT_SMD:SOT-23-5"},
        {"reference": "C1", "value": "1uF", "footprint": "Capacitor_SMD:C_0402_1005Metric"},
        {"reference": "C2", "value": "1uF", "footprint": "Capacitor_SMD:C_0402_1005Metric"},
        {"reference": "J1", "value": "Conn_01x02", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"},
    ],
    "nets": [
        {"name": "VIN", "connections": [
            {"component": "J1", "pin": "1"}, {"component": "U1", "pin": "1"},
            {"component": "U1", "pin": "3"}, {"component": "C1", "pin": "1"},
        ]},
        {"name": "VOUT", "connections": [
            {"component": "U1", "pin": "5"}, {"component": "C2", "pin": "1"},
        ]},
        {"name": "GND", "connections": [
            {"component": "J1", "pin": "2"}, {"component": "U1", "pin": "2"},
            {"component": "C1", "pin": "2"}, {"component": "C2", "pin": "2"},
        ]},
        {"name": "FB", "connections": [
            {"component": "U1", "pin": "4"}, {"component": "C2", "pin": "1"},
        ]},
    ],
}
```

### Decoupling Capacitors
```python
# Add decoupling caps near IC power pins
netlist_data = {
    "components": [
        {"reference": "U1", "value": "MCU", "footprint": "Package_QFP:LQFP-48_7x7mm_P0.5mm"},
        {"reference": "C1", "value": "100nF", "footprint": "Capacitor_SMD:C_0402_1005Metric"},
        {"reference": "C2", "value": "100nF", "footprint": "Capacitor_SMD:C_0402_1005Metric"},
        {"reference": "C3", "value": "10uF", "footprint": "Capacitor_SMD:C_0805_2012Metric"},
    ],
    "nets": [
        {"name": "VCC", "connections": [
            {"component": "U1", "pin": "VDD"}, {"component": "C1", "pin": "1"},
            {"component": "C2", "pin": "1"}, {"component": "C3", "pin": "1"},
        ]},
        {"name": "GND", "connections": [
            {"component": "U1", "pin": "VSS"}, {"component": "C1", "pin": "2"},
            {"component": "C2", "pin": "2"}, {"component": "C3", "pin": "2"},
        ]},
    ],
}
```

---

## Implementation Files

| File | Purpose |
|------|---------|
| `layout_gen/__init__.py` | Public API exports |
| `layout_gen/pipeline.py` | `LayoutPipeline` — orchestrates all steps |
| `layout_gen/models.py` | Dataclasses: `BoardConfig`, `Component`, `Net`, `Netlist`, etc. |
| `layout_gen/netlist_parser.py` | Parse structured dict, KiCad XML, or schematic files |
| `layout_gen/connectivity.py` | Build weighted connectivity graph from netlist |
| `layout_gen/footprint_resolver.py` | Resolve footprint IDs to library paths + courtyard geometry |
| `layout_gen/placer.py` | `ForceDirectedPlacer` — force-directed + simulated annealing |
| `layout_gen/board_builder.py` | Create board, outline, layers, design rules, place footprints, sync nets |
| `layout_gen/router.py` | `ManualRouter` (traces/vias) + `FreeroutingRouter` (auto-routing) |
| `layout_gen/copper_pours.py` | Ground/power plane copper zone generation |
| `layout_gen/units.py` | `mm_to_nm()` / `nm_to_mm()` unit conversion |
| `example_usage.py` | Working examples (voltage divider, LED circuit) |

## Dependencies

- **KiCad 9+** (provides the `pcbnew` Python module — not pip-installable)
- **Freerouting JAR + Java 21+** (optional, for auto-routing only)
- No other external Python dependencies required
