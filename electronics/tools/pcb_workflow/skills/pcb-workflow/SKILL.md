---
name: pcb-workflow
description: Generate PCB layouts from KiCad netlists, place components, or route reviewed boards using the pcb_workflow tool.
---

# PCB Workflow

Use the registered `pcb_workflow` tool for the project's PCB workflow.
Run preprocessing, placement, and routing as separate stages. After placement,
show the placed board and wait for the user's explicit routing approval before
starting the router.

## When to Use

Use this skill when the user requests PCB placement or routing from a KiCad
`.net` netlist using the project's `pcb_workflow` tools.

## Procedure

### 1. Locate the project and inspect its engineering guidance

The workflow package is `tools/pcb_workflow/` in the assembled Agent-EDA
runtime. Resolve the README paths relative to that runtime's `tools/` directory.
The user's circuit project can be in any separate directory.

Discover and call the native `pcb_workflow` tool. It runs one stage per call
inside the dedicated environment, with the checkout's `tools/` as its working
directory. Use absolute input and output paths so the user's working directory
does not affect artifact locations. Its arguments are:

- `stage`: `doctor`, `preprocess`, `place`, or `route`;
- `arguments`: an array of CLI argument strings for that stage;
- `routing_approved`: set to `true` only after explicit approval of the reviewed placement.

For example, check placement dependencies with:

```json
{"stage": "doctor", "arguments": ["--stage", "placement"]}
```

The stage commands below document the CLI argument order. Prefer passing the
arguments after the stage name to the native tool. If it is unavailable,
check `eda plugins show pcb-workflow` and `eda plugins doctor pcb-workflow`.
Report a registration or dependency failure accurately. Terminal execution is
a fallback using the same dedicated environment, from the checkout's `tools/`
directory; never use a global Python or silently substitute another workflow.

Before running a stage, read `pcb_workflow/README.md` for the shared runtime,
input, output, and stage-command contract. At each stage, also read that
tool's README before invoking it:

- preprocessing: `pcb_workflow/placement_preprocessor/README.md`;
- placement: `pcb_workflow/placement_tool/README.md`;
- routing: `pcb_workflow/router_tool/README.md`.

Use these README files as the source of truth for tool-specific arguments,
files, and runtime details. This skill governs the Agent-EDA sequence,
engineering review, and approval gate.

Load the installed `hardware-specialists` skill and select its relevant PCB
layout, high-speed, power, EMC, or manufacturing guidance. Its supporting files
live under `hardware-specialists/references/skill_areas/` in the active skills
folder. Do not assume a top-level `skill_areas/` exists in the tool package.
Apply only guidance relevant to this board and distinguish sourced requirements
from engineering recommendations.

Use the registered `pcb_workflow` tool; `setup.py` creates its private
environment in the assembled Agent-EDA runtime. If the tool reports a missing
interpreter, rerun setup from the product clone. For a host KiCad installation
whose `pcbnew` binding uses a specific interpreter, supply
`--pcb-python "/path/to/kicad-compatible-python"` to setup. The runtime's
`tools/tool_environments/pcb_workflow/.venv` uses `Scripts/python.exe` on
Windows and `bin/python` on Linux/macOS.

For a nonstandard KiCad install, set any needed overrides before running the
workflow: `KICAD_ROOT`, `KICAD_PYTHON_PATH`, `KICAD_BIN`, `KICAD_CLI`, and
`KICAD_FOOTPRINTS`. Their meanings and shell-specific examples are in
`pcb_workflow/README.md`. The helper searches common Linux, Windows, and macOS
locations automatically when overrides are absent.

Before placement, run `python -m pcb_workflow doctor --stage placement` with
the selected environment. Before routing, after approval, run
`python -m pcb_workflow doctor --stage routing`. In Windows PowerShell, invoke
these as `& .\tool_environments\pcb_workflow\.venv\Scripts\python.exe -m pcb_workflow doctor --stage placement`
or the same command with `--stage routing`. The checks validate the active
Python, `pcbnew` import and compatibility, the packages needed by that stage,
and its KiCad resources. Placement and routing also perform their required
preflight automatically. If a dependency is missing or the binding is
incompatible, stop and report the diagnostic; do not switch interpreters or
claim the stage ran.

### 2. Preprocess the netlist

Confirm that the input is an existing KiCad `.net` file. Choose a stable
filename prefix and output directory, then run:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow preprocess <netlist.net> \
  --out-dir generated/<prefix>
```

Pass `--board`, `--project`, `--rules`, and `--footprints-dir` when those
matching inputs are available. Do not modify the source files.

Read the preprocessing result manifest and inspect `components.csv`,
`nets.csv`, `net_nodes.csv`, `footprints_list.csv`, and
`<prefix>_footprints.json`. Check component, net, and connection counts,
missing footprints, unresolved footprint geometry, and any board/netlist
reference mismatches. Resolve material footprint gaps or report them before
placement; do not present approximate geometry as verified.

Immediately after this analysis, present the design facts and a reasoned
provisional board-parameter recommendation before asking the user anything.
Estimate a compact board outline from resolved footprint areas, component
count, connector placement, and functional connectivity/clusters; recommend
layer count, copper/fill approach, clearances, outline clearance, and mechanical
features only to the extent the outputs and relevant engineering guidance
support them. Explicitly separate netlist-derived facts, sourced rules, and
engineering assumptions, and explain uncertainty. Preprocessing a netlist
alone does not establish board dimensions, stackup, fabrication rules, or
mechanical constraints, so do not describe inferred values as requirements or
verified settings. Then ask the user to confirm or revise the proposed
parameters; if essential values remain unsupported, do not proceed to placement
until the user supplies them or authorizes provisional best-judgment values.

### 3. Prepare and explain the placement configuration

Use the analysis and provisional parameter recommendation presented immediately
after preprocessing. Refine it with relevant engineering skills and make the
placement configuration traceable to the user's confirmation, supplied project
data, or explicitly authorized assumptions. State the evidence or assumption
behind each value.

The netlist does not define board dimensions, mechanical constraints, stackup,
or all fabrication rules. Prefer values supplied by the user, the matching
board/project, or fabrication requirements. If a required value has no
defensible source, ask the user for it before placement. If the user has
explicitly authorized best-judgment assumptions, label them as provisional
and include them in the placement summary.

Ensure the prepared-input directory contains:

- `<prefix>_board.json` with positive `width` and `height` values in mm;
- `<prefix>_drc.json` with `clearance` and `outline_clearance` values in mm;
- `<prefix>_layers.json` when layer or fill settings are specified;
- `<prefix>_mechanical.json` when holes or keepouts are specified;
- `<prefix>_placement_constraints.json` when placement constraints are
  specified.

Use the formats documented in `pcb_workflow/README.md` and the tool
documentation. Validate JSON before placement. Do not invent component or net
names; use exact references and names from the preprocessor outputs.

### 4. Run placement only

Run placement in the shared design directory:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow place \
  generated/<prefix> <prefix> \
  --out-dir generated/<prefix>
```

Pass `--footprints-dir` if a non-default KiCad footprint root was used during
preprocessing. Inspect `<prefix>_placement_result.json` and require
`placement_complete` plus an existing `<prefix>_placed.kicad_pcb` before
presenting it. Placement creates no tracks or vias. **Do not invoke the route
stage during this step.**

Summarize the board dimensions, placed component count, unresolved assumptions,
and the placement artifact. Ask the user whether to route this placed board.
Wait for a clear affirmative answer. A general request to generate a PCB is not
approval to start routing at this gate. If placement or its constraints
change, present the new result and ask again.

### 5. Route only after approval

After explicit approval, route the exact placed board from the placement
output. Use the same DRC JSON that was reviewed for placement:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow route \
  generated/<prefix>/<prefix>_placed.kicad_pcb \
  --out generated/<prefix>/<prefix>_routed.kicad_pcb \
  --drc generated/<prefix>/<prefix>_drc.json
```

This workflow uses LineExplore as its only routing engine. Do not start another
router or substitute a fallback. The router writes a separate output board and
preserves the placed input. A partial result is still an inspection artifact;
report its status, failed nets, open connections, routing time, and DRC results
from the routing sidecars. Do not describe a partial board as completely
routed or fabrication-ready.

## Completion Criteria

Placement is complete only when its result manifest reports
`placement_complete` and the placed board exists. Routing is complete only
when the router result reports `complete`; report a `partial` result as such.
Report native DRC status and unresolved connectivity from the generated
artifacts. Board-file existence alone does not establish routing completion or
manufacturing readiness.
