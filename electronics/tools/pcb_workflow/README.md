# Standalone PCB workflow

This directory contains three standalone stages:

1. `placement_preprocessor` parses a KiCad `.net` netlist and resolves assigned
   footprint metadata. An optional existing board, project, and rules file can
   provide additional geometry and rule data.
2. `placement_tool` consumes the prepared CSV/JSON files plus board and DRC
   configuration, then creates a placed, unrouted `.kicad_pcb`.
3. `router_tool` accepts a placed board and routes a separate output board with
   LineExplore only.

The package entry point dispatches **one stage per invocation**. Completing the
placement command never starts routing. This lets a reviewer inspect the
placed board and explicitly approve routing before the router is invoked.

## Agent-EDA skill

The `plugin.yaml` and `register(ctx)` expose the `pcb_workflow` model tool and
the bundled workflow skill through Agent EDA's plugin discovery. Enable it with
`agent-eda plugins enable pcb-workflow`. The plugin bridge uses `tools/tool_environments/pcb_workflow/.venv` and passes each request to the existing stage CLI as an argument list;
it does not install the workflow requirements into Agent EDA's environment.

The workflow skill is also in
`pcb_workflow/skills/pcb-workflow/SKILL.md`. If you prefer using the skill
without enabling the native plugin, add the project skill folder to Agent-EDA's
configured external skill directories. For an Agent-EDA runtime that supports
the `skills.external_dirs` setting, use:

```yaml
skills:
  external_dirs:
    - /absolute/path/to/project/pcb_workflow/skills
```

Start a new Agent-EDA session after configuring the external directory. The
native plugin exposes one stage per tool call; preprocessing, placement, and
routing are never chained. Review the placed board and obtain explicit user
approval before calling the route stage. Set `routing_approved=true` only after
that approval. The plugin checks this flag and refuses routing otherwise.

## Requirements

- KiCad's `pcbnew` Python binding must be available and compatible with the selected Python.
- KiCad footprint libraries are supplied by the host or Agent-EDA.
- `kicad-cli` is required for the router's native DRC audit.
- Python packages are installed into this tool's private environment.

The workflow detects KiCad on Linux, Windows, and macOS; it does not install KiCad.
`setup.py` in the Agent-EDA product clone creates this private environment.
For a manual repair, run the following from the assembled runtime root:

```bash
python tools/setup_tool_environments.py --tool pcb_workflow
```

If KiCad's `pcbnew` binding uses a different Python version, select a compatible
interpreter and recreate the environment:

```bash
python tools/setup_tool_environments.py --tool pcb_workflow --pcb-python "/path/to/kicad-compatible-python" --recreate
```

The tool environment is `tools/tool_environments/pcb_workflow/.venv`. On
Windows, its interpreter is under `Scripts/python.exe`; on Linux and macOS it
is under `bin/python`. The workflow searches common KiCad locations. For a
custom installation, set `KICAD_ROOT`, `KICAD_PYTHON_PATH`, `KICAD_BIN`,
`KICAD_CLI`, and/or `KICAD_FOOTPRINTS` as documented below.

Run the stage-specific environment check before placement or routing. To check
all dependencies at once, use `--stage all` (or omit `--stage`):

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow doctor --stage placement
```

### KiCad path overrides

The helper searches standard installation locations. Set an override when
Agent-EDA or the user has installed KiCad elsewhere:

| Variable | Purpose |
| --- | --- |
| `KICAD_ROOT` | KiCad installation root; on macOS this may be the `.app` bundle or its `Contents` directory |
| `KICAD_PYTHON_PATH` | Directory containing `pcbnew.py` and its native extension |
| `KICAD_BIN` | KiCad binary/library directory; used for Windows DLL lookup |
| `KICAD_CLI` | `kicad-cli` executable path or the directory containing it |
| `KICAD_FOOTPRINTS` | Footprint root containing folders such as `Resistor_SMD.pretty` |

Bash:

```bash
export KICAD_ROOT="/custom/path/to/kicad"
export KICAD_PYTHON_PATH="/custom/path/to/kicad/python-bindings"
export KICAD_BIN="/custom/path/to/kicad/bin"
export KICAD_CLI="/custom/path/to/kicad/bin/kicad-cli"
export KICAD_FOOTPRINTS="/custom/path/to/kicad/footprints"
```

PowerShell:

```powershell
$env:KICAD_ROOT = "C:\custom\KiCad"
$env:KICAD_PYTHON_PATH = "C:\custom\KiCad\python-bindings"
$env:KICAD_BIN = "C:\custom\KiCad\bin"
$env:KICAD_CLI = "C:\custom\KiCad\bin\kicad-cli.exe"
$env:KICAD_FOOTPRINTS = "C:\custom\KiCad\share\kicad\footprints"
```

`doctor` reports the active Python, detected binding, applicable CLI and
footprint roots, Python packages, and import/configuration errors. Placement and routing repeat the
relevant preflight automatically and stop with configuration guidance if a
required dependency is unavailable.

Run the commands from the repository root so Python can import `pcb_workflow`.

## Stage 1: preprocess

Netlist only:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow preprocess design.net \
  --out-dir generated/design
```

With optional board and project inputs:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow preprocess design.net \
  --board design.kicad_pcb \
  --project design.kicad_pro \
  --rules design.kicad_dru \
  --out-dir generated/design
```

The preprocessor writes `components.csv`, `nets.csv`, `net_nodes.csv`,
`footprints_list.csv`, `<prefix>_footprints.json`, and a preprocessing result
manifest. Board dimensions, layers, mechanical details, and DRC values are
included only when they can be extracted from supplied board/project inputs.
The netlist alone does not define board size or all required DRC rules.

Before placement, make sure the prepared-input directory contains:

- `<prefix>_board.json` with positive width and height in millimeters;
- `<prefix>_drc.json` with `clearance` and `outline_clearance` in millimeters;
- optional `<prefix>_layers.json`, `<prefix>_mechanical.json`, and
  `<prefix>_placement_constraints.json` when those settings apply.

Create those JSON files from user requirements or valid project/board data. If
a required value has no defensible source, ask the user. Use a labeled
provisional assumption only when the user has authorized best-judgment
assumptions. Do not treat netlist data as the source for absent mechanical,
board-size, or fabrication rules.

## Stage 2: placement

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow place \
  generated/design design \
  --out-dir generated/design
```

The placement tool validates and normalizes structured placement inputs, then
writes `<prefix>_placement.json`, `<prefix>_placed.kicad_pcb`, and a placement
result manifest. Normalized board and mechanical configurations use the names
`<prefix>_placement_board.json` and `<prefix>_placement_mechanical.json`,
preserving the original input files. This board has no routing tracks or vias.
Review its manifest and board before deciding whether routing should start.

## Approval gate

Routing is a separate command. Do not run it until the user has reviewed the
placement and explicitly approved routing that placed board. Approval applies
to the exact board shown; if placement or its constraints change, present the
new placement for approval.

## Stage 3: route after approval

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow route \
  generated/design/design_placed.kicad_pcb \
  --out generated/design/design_routed.kicad_pcb \
  --drc generated/design/design_drc.json
```

The router uses LineExplore only; this workflow has no alternate-router
fallback. The placed source board is preserved. A partial route still produces
a board named `<prefix>_partially_routed.kicad_pcb` and a result sidecar; it
must be reported as partial, with failed nets
and open connections from the result. A complete route still requires review of
the native DRC results before any manufacturing-ready claim.

## Output layout

```text
generated/<prefix>/
  components.csv, nets.csv, net_nodes.csv, footprints_list.csv
  <prefix>_board.json and other preprocessing inputs and results
  <prefix>_placement_board.json and <prefix>_placement_mechanical.json
  <prefix>_placement.json and <prefix>_placed.kicad_pcb
  <prefix>_routed.kicad_pcb (or <prefix>_partially_routed.kicad_pcb) and routing sidecars
```

## Individual tool commands

Each stage remains directly callable as a Python module:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.placement_preprocessor design.net --out-dir generated/design
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.placement_tool generated/design design --out-dir generated/design
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.router_tool generated/design/design_placed.kicad_pcb --out generated/design/design_routed.kicad_pcb
```

The top-level `python -m pcb_workflow` command accepts `preprocess`, `place`,
`route`, or `doctor`. Each design command runs one stage only.
