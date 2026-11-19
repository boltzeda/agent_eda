# Standalone PCB placement tool

This tool accepts prepared CSV and JSON inputs, places the components, and exports a placed,
unrouted `.kicad_pcb`. It creates no tracks or vias and runs no router. It does
not change source input files. The original plugin files are not imported or
modified by this copy.

## Entry point

From the repository root:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.placement_tool <prepared-input-dir> <prefix>
```

In Windows PowerShell, use
`& .\tool_environments\pcb_workflow\.venv\Scripts\python.exe -m pcb_workflow.placement_tool ...`.
See the parent workflow README for environment setup, KiCad path overrides,
and the `doctor` preflight command.

First use the separate [`placement_preprocessor`](../placement_preprocessor/README.md)
with a KiCad `.net` file, optionally adding its `.kicad_pcb` to provide physical
footprint geometry and board data. The preprocessor and placement tool are
separate commands; neither is integrated with the existing PCB layout plugin. A DRC JSON
can be extracted from optional project/board rule inputs; if those inputs do
not provide the required values, supply them separately. In netlist-only mode,
board JSON must also be supplied separately.

For example:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.placement_tool generated/my_board my_board \
  --out-dir generated/my_board
```

The default output directory is the prepared-input directory.
The tool stages its inputs into a temporary working directory so the placement
pipeline's normalized configuration writes do not alter the supplied inputs.

Optional arguments:

```text
--out-dir <directory>          choose the output directory
--generations <count>          genetic algorithm generations (default 100)
--population <count>           genetic algorithm population (default 50)
--placement-config <file.json> placement hints and constraints
--allow-bottom                 permit bottom-side SMD placement
--bottom-bias <value>          cost bias for bottom-side placement
--footprints-dir <directory>  KiCad footprint library root
```

The output includes `<prefix>_placement.json`, `<prefix>_placed.kicad_pcb`,
`<prefix>_placement_result.json`, and the final board/mechanical configuration
JSON (`<prefix>_placement_board.json` and `<prefix>_placement_mechanical.json`)
when the pipeline writes them; the original input configurations remain unchanged.
KiCad board export runs in a separate process. The board is placed but unrouted
and is not fabrication-ready. If
placement constraints or board export fail, the CLI returns exit code 2 and
preserves any placement JSON already produced.

## Input files

Required in the prepared input directory:

- `components.csv`
- `<prefix>_footprints.json`
- `<prefix>_board.json`
- `<prefix>_drc.json`

Recommended inputs:

- `nets.csv` and `net_nodes.csv` for connectivity-based clustering and the
  approximate pad-escape/channel feasibility check
- `<prefix>_layers.json` for copper-fill awareness in that feasibility check
- `<prefix>_mechanical.json` for holes and keepouts
- `<prefix>_placement_constraints.json` for placement policy, cluster hints,
  side hints, and relative constraints

Footprint metadata should include exact geometry or resolved footprint paths
where available. The placement pipeline can use standard KiCad footprint
libraries to measure dimensions. Board export loads assigned footprint IDs
from those libraries; set `KICAD_FOOTPRINTS` or pass `--footprints-dir` for a
custom library root.

## Dependencies

Use a Python version compatible with the pinned packages and the host KiCad
`pcbnew` binding. KiCad is supplied by the host or Agent-EDA; this tool only
discovers and validates it. Linux/macOS setup:

```bash
python tools/setup_tool_environments.py --tool pcb_workflow
```

Windows PowerShell:

```powershell
python tools/setup_tool_environments.py --tool pcb_workflow
```

Run `python -m pcb_workflow doctor --stage placement` using that environment
before placement. In PowerShell, use
`& .\tool_environments\pcb_workflow\.venv\Scripts\python.exe -m pcb_workflow doctor --stage placement`.
Run commands from the repository root.

The placement algorithm can use PyTorch on CPU; CUDA is not required. Board
generation requires a Python-compatible KiCad `pcbnew` binding and footprint
libraries.
Routing remains the responsibility of the separate `router_tool`.
