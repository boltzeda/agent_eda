# Standalone placement preprocessor

This tool parses a KiCad schematic `.net` netlist and supports two input
modes:

1. Netlist only: extract component, footprint-ID, and connectivity data.
2. Netlist plus `.kicad_pcb`: use the netlist as the authority for components
   and connectivity, then enrich matching references with footprint geometry
   and board data from the PCB.

Matching `.kicad_pro` project settings and `.kicad_dru` custom rules can also
be supplied. Both files are optional; omitting them does not prevent
preprocessing. Inputs are read-only. This tool does not place components or
route a board. When run alone, the parser requires Python 3.10 or newer and
uses only the standard library. Placement and routing additionally require a
Python version compatible with the installed KiCad `pcbnew` binding and the
workflow packages.

## Run

Netlist only:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.placement_preprocessor path/to/design.net \
  --out-dir path/to/prepared_inputs
```

Netlist plus board and optional rule files:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.placement_preprocessor path/to/design.net \
  --board path/to/design.kicad_pcb \
  --project path/to/design.kicad_pro \
  --rules path/to/design.kicad_dru \
  --out-dir path/to/prepared_inputs
```

`--project` and `--rules` can be used independently and neither is required.
The prefix defaults to the netlist filename without `.net`; use `--prefix` to
override it. A supplied board needs a usable `Edge.Cuts` outline.

The examples use the Linux/macOS venv path. In Windows PowerShell, use
`& .\tool_environments\pcb_workflow\.venv\Scripts\python.exe -m pcb_workflow.placement_preprocessor ...`.

## Outputs in both modes

- `components.csv`: reference, value, assigned footprint ID, datasheet,
  description, symbol library/part, and schematic sheet path.
- `nets.csv`: net code, name, fanout, and net-class name.
- `net_nodes.csv`: net name/code and each connected reference and pin number,
  with pin function and electrical pin type when present in the netlist.
- `footprints_list.csv`: one row per component with an assigned footprint;
  includes footprint library/name, description, symbol footprint filters, and
  symbol pin count. These are identifiers and metadata, not physical geometry.
- `<prefix>_footprints.json`: per-reference footprint metadata for placement.
  The preprocessor uses each assigned footprint ID from the netlist to find the
  matching `.kicad_mod` in a KiCad footprint library and extracts its dimensions
  and pad geometry. If the library footprint cannot be resolved, a matching
  footprint in the optional board can provide geometry. Unresolved references
  are counted in the manifest; the preprocessor does not invent dimensions.
- `<prefix>_placement_preprocessor_result.json`: counts, input mode, missing
  placement inputs, and netlist/board reference mismatches.

## Additional outputs when a board is supplied

- `<prefix>_board.json`: dimensions from the board's `Edge.Cuts` bounds.
- `<prefix>_layers.json`: copper-layer list and zone-fill mapping.
- `<prefix>_mechanical.json`: non-plated holes and keepout bounds.
- `<prefix>_footprints.json` includes embedded footprint geometry for matching
  references.
- Board-embedded setup values and zone-local clearances are included in
  `<prefix>_drc.json` when any are present.

Connectivity in `nets.csv` and `net_nodes.csv` always comes from the netlist.
Board-only references are reported in the manifest and are not added as
netlist components.

## Footprint library lookup

The netlist supplies the footprint ID (for example, `Resistor_SMD:R_0603_1608Metric`);
physical dimensions come from the corresponding KiCad library file, not from
dimensions stored in the netlist. The preprocessor searches `KICAD_FOOTPRINTS`,
`KISYSMOD`, KiCad footprint-directory environment variables, and standard
Linux, Windows, and macOS installation locations. To select a specific library root, pass
`--footprints-dir <directory>`; it must contain folders such as
`Resistor_SMD.pretty/`.

Example:

```bash
tool_environments/pcb_workflow/.venv/bin/python -m pcb_workflow.placement_preprocessor path/to/design.net \
  --footprints-dir <KiCad-footprint-root> \
  --out-dir path/to/prepared_inputs
```

For KiCad installed outside standard locations, configure `KICAD_ROOT` and, if
needed, `KICAD_FOOTPRINTS`. The workflow discovers paths; Agent-EDA supplies
the KiCad installation.

## Optional KiCad rule inputs

- `--project <file.kicad_pro>` reads board design-rule minimums, severities,
  default-netclass values, netclass assignments, and netclass-pattern matches
  where the netlist provides matching net names/classes.
- `--rules <file.kicad_dru>` writes `<prefix>_custom_rules.json` containing
  custom rule names, conditions, constraints, and their source expressions.
  Arbitrary custom expressions are preserved for reference; this parser does
  not translate them into placement or router behavior.
- `<prefix>_drc.json` is written when rule information is found. Missing values
  such as `clearance` or `outline_clearance` are reported in the manifest and
  are not guessed. This keeps rule-file parsing optional.

`placement_tool` requires `<prefix>_board.json` and `<prefix>_drc.json` with at
least `clearance` and `outline_clearance`. In netlist-only mode, supply board
configuration separately. If optional rule inputs are omitted or do not
contain those values, preprocessing still succeeds, while the manifest lists
what placement still needs.
