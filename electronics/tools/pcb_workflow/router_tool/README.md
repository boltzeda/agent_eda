# PCB router

The router accepts a placed, unrouted KiCad board and writes a separate routed
board. It preserves the placed source board, does not change component
placement, and uses LineExplore only.

## Run

From the repository root:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.router_tool path/to/placed_board.kicad_pcb \
  --out path/to/routed_board.kicad_pcb \
  --drc path/to/board_drc.json
```

In Windows PowerShell, use
`& .\tool_environments\pcb_workflow\.venv\Scripts\python.exe -m pcb_workflow.router_tool ...`.
See the parent workflow README for environment setup, KiCad path overrides,
and the `doctor` preflight command.

The DRC JSON supplies the board's routing rules. See the workflow README for
the staged process and the placement preprocessor README for available rule
inputs.

Optional arguments:

```text
--out <path>       choose the routed board output path
--drc <path.json>  provide routing-rule values
--via-cost <value> set the via cost (default 2.0)
--quiet            reduce progress output
```

## Outputs and status

The router writes the routed `.kicad_pcb`, a routing-result JSON sidecar, and a
route-details sidecar. A complete result uses the requested output name; a
partial result uses the `_partially_routed.kicad_pcb` suffix, even when `--out`
is specified. The result manifest records the actual board path. The default
complete output for `<prefix>_placed.kicad_pcb` is `<prefix>_routed.kicad_pcb`.
The result includes routing status, failed nets,
connectivity statistics, routing time, and DRC statistics.

Exit codes:

- `0`: routing complete;
- `2`: routing partial; the routed board is retained for inspection;
- `1`: input or execution error.

The routing process performs native DRC checks and reports DRC statistics.
Review those results, and run an independent native DRC review before treating
the board as ready for manufacturing. A partial result is not manufacturing-
ready. The router does not switch to another routing engine.

## Runtime requirements

Routing requires the packages in `pcb_workflow/requirements.txt`, a Python-
compatible KiCad `pcbnew` binding, and `kicad-cli`. KiCad is supplied by the
host or Agent-EDA and is not installed by this tool. Linux/macOS setup:

```bash
python tools/setup_tool_environments.py --tool pcb_workflow
```

Windows PowerShell:

```powershell
python tools/setup_tool_environments.py --tool pcb_workflow
```

Run `python -m pcb_workflow doctor --stage routing` with the selected
environment before routing; use the Windows interpreter path above in
PowerShell.
