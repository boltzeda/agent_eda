---
name: kicad-schematic-generation
description: Create and verify native KiCad schematics (.kicad_sch) from circuit requests using the kicad_generation plugin.
version: 0.1.0
author: boltzmann17, Agent EDA
license: MIT
platforms: [linux, macos, windows]
metadata:
  agent-eda:
    tags: [electronics, schematic, kicad, circuit-synth]
    category: eda
    related_skills: [intent-understanding, block-expansion, circuit-simulation, model-acquisition, hardware-specialists]
---

## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

# KiCad Schematic Generation Skill

Use this workflow to generate a native KiCad project from an approved circuit
topology. It writes a `generate.py` script using `circuit-synth`: the script defines
connectivity, and circuit-synth places symbols and routes wires. Generation does not
correct design mistakes.

## When to Use

- The user asks in natural language to create, generate, or export an editable KiCad schematic (`.kicad_sch`) from a circuit, including a 555 timer circuit.
- For unqualified native KiCad schematic requests, this is the default workflow; use `schematic_gen` only when the user explicitly requests that skill.
- A KiCad schematic is the requested output and the selected source workflow is
  `circuit-synth`.
- Use `eda/tscircuit-design` instead when the user explicitly requests a tscircuit
  project or that workflow. Do not mix generators for one artifact.
- Use the Agent-EDA `kicad_generation` plugin to run Circuit Synth generation in its
  plugin-owned Python environment.

## Prerequisites

- Check the active Agent-EDA CLI before changing plugin settings: run
  `eda plugins show kicad_generation` and `eda tools --summary`. If the plugin is
  disabled, enable it with `eda plugins enable kicad_generation`; an “already
  enabled” response means continue without changing it. Confirm `kicad_generation`
  is enabled for CLI and registers `kicad_schematic_setup` and
  `kicad_schematic_generate` (use `eda plugins doctor kicad_generation` if the
  tools are missing). If the plugin was installed or enabled after this chat
  started, restart `eda` so the active session receives its tool definitions;
  `/reload-skills` reloads skills, not plugin tools. Run `kicad_schematic_setup`
  once on this checkout and require a successful result before generation. If
  setup or tool discovery fails, report the actual error and stop; do not
  substitute an SVG or other non-native output for the requested KiCad project.
- The plugin runs on the Agent-EDA host. Keep the project directory accessible to
  that host; a project that exists only on a remote terminal backend is not visible
  to the generation tool. Install KiCad separately so `kicad-cli` is available for
  verification.
- Use `read_file`, `search_files`, and `terminal` to inspect the project, installed
  tools, and their current CLI/API help. Use `write_file` or `patch` to edit the
  user's project.
- Start with approved topology, exact parts/packages, and datasheet-based pin
  mappings. For validated status, include required upstream simulation evidence.
- Treat a schematic as verified only after checking its native file, connectivity,
  rendering, and ERC. Report any missing evidence as unverified.
- Run generation with `kicad_schematic_generate`, which executes the project-local
  `generate.py` using the plugin's private Python environment. Use the Python path
  returned by `kicad_schematic_setup` for other Circuit Synth commands, such as the
  component resolver. KiCad CLI remains a separately installed host tool.
- Use `intent-understanding`, `block-expansion`, and relevant hardware/model skills
  for their respective design, pin, and model handoffs.

## How to Run

Inspect the project instructions, source, and libraries first. Check `kicad-cli` and
the Circuit Synth API before relying on version-specific behavior. The plugin keeps
Circuit Synth dependencies out of Agent-EDA's core environment. Use the resolver and
generator contract below.

## Quick Reference

| Stage | Input | Output / completion |
|---|---|---|
| Preflight | Approved topology and pin audit | Exact symbols, packages, footprints, libraries resolved |
| Generate | `generate.py` using `circuit-synth` | Hierarchical `.kicad_sch`, `.kicad_pro`, `.net`, source, libraries |
| Verify | Fresh generated project | ERC/render/netlist evidence; blockers reported |
| Handoff | Reviewed schematic and confirmed board constraints | PCB work only after explicit layout request |

### Inputs and status

- Confirmed topology, exact part numbers, packages, and datasheet-based pin mappings.
- **Validated** means approved topology, datasheet pin audit, required upstream
  simulation evidence, and no ERC design errors. When reusing a model, match the
  manufacturer, exact MPN, dialect, pin order, hash, and source. **Preliminary** is
  allowed only when the user explicitly disables or authorizes skipping simulation.
- Preliminary status relaxes only the simulation gate. It still requires correct
  symbols, footprints, values, and pin connectivity.
- If topology, pin mapping, footprint, or symbol identity is unresolved, stop and
  report the blocker. Never hide uncertainty with an unrelated symbol or guessed
  connection.

## Procedure

1. **Inspect the project.** Read its instructions and existing source. Keep all files
   and conventions intact. Check the installed KiCad CLI and bundled Circuit Synth
   API when version-specific behavior matters. If the plugin environment is absent,
   run `kicad_schematic_setup` before proceeding.
2. **Reuse libraries.** Reuse restored/generated symbols and footprints when
   available. Restore files with a recorded `local_path` in place; copy flat restored
   files into the current circuit's `kicad_libraries/` while preserving `.kicad_sym`
   names and `<Lib>.pretty/` structure. Register one symbol-table row per symbol
   library and one footprint-table row per footprint library, idempotently. Before
   importing circuit-synth, set `KICAD_SYMBOL_DIR` to include stock KiCad symbols and
   the current circuit's generated libraries.
3. **Resolve every part.** Run the resolver with the Python path returned by
   `kicad_schematic_setup` as
   `<PLUGIN_PYTHON> -m circuit_synth.kicad.resolve_components <Part1> <Part2> ...`
   to find exact KiCad symbols. Match any alternate symbol to authoritative package
   and pin evidence. Audit the resolver's physical pin numbers against both the
   datasheet pinout and footprint pad numbers; do not infer correspondence from
   symbol pin order or the pin's KiCad electrical-type label alone.
   Verify each footprint exists. If a symbol is genuinely missing, stop unless the
   user authorizes a project-local, clearly marked preliminary symbol derived from
   reviewed datasheet data.
4. **Write and run the generator.** Save `generate.py` and all outputs together in
   `generated/<circuit>/schematic/`. Derive output paths from `__file__`, not the
   process working directory. Reuse project libraries and bundle every non-stock
   symbol and footprint library into the project. Run the script with
   `kicad_schematic_generate`, passing its path; that tool launches it with the
   plugin's private Python and uses the script's directory as its working directory.
5. **Generate hierarchically.** Use a root `@circuit` and one child `@circuit` for
   each functional block. Use globally unique reference designators. Pass signal nets
   between blocks intentionally; use global power symbols for global rails rather than
   ordinary child signal ports.
4. **Verify the output.** Check generation status, project files, hierarchy,
   symbol/footprint resolution, netlist, schematic render, and fresh ERC. Compare the
   exported netlist against the approved pin-to-net connectivity, then visually inspect
   the rendered sheet for legible labels, recognizable symbols, and missing or
   unintended connections. Fix source or library issues, regenerate, and rerun affected
   checks. Continue this repair loop before handing off whenever the finding is
   resolvable; a successful generator run is not completion while ERC design errors
   remain. If a blocker cannot be resolved, label the artifact unverified and state the
   remaining findings rather than presenting it as done. Never hand-edit generated
   `.kicad_sch` files to hide a source error. Do not alter circuit connectivity solely
   to silence ERC; for an unused pin or a stock-symbol electrical-type mismatch, use a
   datasheet-correct symbol and explicit no-connect representation, or report the
   generator limitation and keep the schematic stage unverified until resolved.
7. **Report and stop at the schematic checkpoint.** Give artifact paths,
   validated/preliminary status, component and net summary, pin and footprint
   coverage, and a user-readable ERC result. If layout is requested later, assess the
   application constraints, state facts/derived values/assumptions, propose board
   parameters, and wait for confirmation plus an explicit layout request before
   placement or routing.

### Generator contract

Use the installed public API; confirm version-specific signatures rather than
inventing them.

```python
from pathlib import Path
from circuit_synth import Component, Net, circuit
from circuit_synth.kicad.sch_gen.main_generator import SchematicGenerator

@circuit(name="input_block")
def input_block(vin, gnd, signal):
    source = Component(symbol="Device:...", ref="J1", value="INPUT",
                       footprint="Connector:...")
    source["1"] += vin
    source["2"] += gnd
    source["3"] += signal

@circuit(name="example")
def build():
    vin, gnd, signal = Net("+5V"), Net("GND"), Net("SIGNAL")
    input_block(vin, gnd, signal)

def main():
    out = Path(__file__).resolve().parent
    design = build()
    json_path = out / "example.json"
    design.generate_json_netlist(str(json_path))
    result = SchematicGenerator(str(out.parent), out.name).generate_project(
        json_file=str(json_path), force_regenerate=True, generate_pcb=False,
        placement_algorithm="hierarchical",
        schematic_placement="connection_aware",
    )
    if not result.get("success", True):
        raise RuntimeError(result.get("error", "KiCad generation failed"))

if __name__ == "__main__":
    main()
```

The example is a shape, not a design: replace symbols, pins, nets, values, and
footprints with verified project data. The public `Component.set_placement(...)`
method may provide group, order, preferred-position, or fixed-position hints; inspect
the rendered result because hints do not prove legal placement.

The plugin bundles a snapshot of the modified Circuit Synth source present when the
plugin was prepared. Updating the sibling Circuit Synth checkout does not update this
copy; sync and review the vendored source before releasing those later changes.

## Pitfalls

### Electrical and library rules

- Use the exact KiCad `Library:Symbol` ID returned by the resolver. Keep the
  purchasing MPN in the IC value; use electrical values for passives and short labels
  for connectors. Put rationale in descriptions/comments, never values such as `TBD`,
  `REVIEW`, or full sentences.
- Every BOM component needs a real, existing footprint. Schematic-only ERC markers
  such as `PWR_FLAG` are the exception; keep them out of the BOM. Connect every
  physical IC pin per its datasheet or mark an explicitly permitted no-connect.
  Connect all power pins. Do not guess by symbol pin order or by an unrelated family
  member.
- Never equate a successful export with a correct schematic: parsers may flatten
  subcircuits, map functional pin order incorrectly, or route unrelated nets together.
  Verify each IC's physical pin-to-net mapping in the exported netlist and rendered
  sheet before sharing the artifact.
- For op-amp/macromodel parts, audit the SPICE subcircuit order and package pin
  numbers separately. For example, an LM741 model's functional order is non-inverting,
  inverting, positive supply, negative supply, output; this is not the package pin
  numbering. Handle offset-null and NC pins explicitly. See the
  [LM741 example](references/lm741-example.md).
- Use `component[pin] += net` with a `Net` or `Pin`; never wire by bare string or
  integer. Use pin names where reliable, otherwise verified physical pin numbers.
  `PinGroup` connects every physical pin sharing that name.
- Give each net one definition and each reference designator a unique project-wide
  identity. Keep functional block net names distinct unless intentionally shared.
- For global rails such as `GND` and `+5V`, prefer global power symbols inside child
  sheets over passing them as ordinary child ports; this avoids unmatched hierarchical
  - A connector alone does not satisfy KiCad's power-source ERC rule: use a real
    power-output source or a correctly connected `PWR_FLAG` on an externally supplied
    rail. `PWR_FLAG` marks an ERC source; it is not a physical supply.
  - Do not ground, pull up, or otherwise repurpose an unused IC pin just to clear an
    ERC finding. Resolve the pin's datasheet function and symbol electrical type first;
    an electrical-type mismatch in a stock symbol is a library issue, not permission to
    change the circuit.
- Keep all generated files co-located with `generate.py`, including root/child
  `.kicad_sch`, `.kicad_pro`, `.net`, JSON, and verification evidence. The native
  schematic suffix is `.kicad_sch`.
- Bundle every referenced non-stock `.kicad_sym` and `.pretty` library under
  `schematic/libraries/`. Point project-local `sym-lib-table` and `fp-lib-table`
  entries to `${KIPRJMOD}/libraries/...`; preserve stock library entries. Recheck
  these tables after every regeneration and verify the project works without relying
  on an upstream custom library directory. If the generator rewrites custom symbol
  entries to a stock-library path, restore the project-local paths before ERC.

## References

Read only the reference matching the issue:

- Custom preliminary symbol or stale local symbol copy:
  [preliminary-symbol-erc.md](references/preliminary-symbol-erc.md).
- Hierarchical ERC attribution, power rails, or library-table failures:
  [hierarchical-erc-debug.md](references/hierarchical-erc-debug.md).
- Exact symbol/footprint and verification gate for small regulators:
  [exact-part-symbol-and-verification-gate.md](references/exact-part-symbol-and-verification-gate.md).
- Isolated current/temperature sensor circuits:
  [isolated-sensor-generation-pitfalls.md](references/isolated-sensor-generation-pitfalls.md).
- Focused verification when the project suite does not cover the changed artifact:
  [ad-hoc-sidecar-verification.md](references/ad-hoc-sidecar-verification.md).
- For uncertain circuit-synth API signatures, consult the installed package's
  canonical API reference (`llm.txt` and `GENERATE_PY_REFERENCE.md`) when present.

## Verification

Generation success or file existence alone is not verification. Keep fresh evidence
under `generated/<circuit>/verification/` (or the project's established verification
directory); never overwrite the only source of verification evidence with an
unrecorded temporary check.

1. Confirm the generator exited successfully and its result reports success. Confirm
   the root and child schematics, project, netlist, generator, and required libraries
   exist and are non-empty.
2. Confirm the hierarchy matches the functional blocks and generation used
   `placement_algorithm="hierarchical"` plus `schematic_placement="connection_aware"`.
3. Run machine-readable ERC on the root schematic, for example `kicad-cli sch erc
   --format json -o <verification>/erc.json <root>.kicad_sch`. Classify every finding
   as a design error, permitted datasheet no-connect, or environment/library note. Fix
   design errors and actionable library issues in the source, regenerate, then rerun
   checks. Do not export or hand off a schematic with unresolved design errors. Limit
   the repair loop to three iterations; save each report and count change, then report
   remaining violations verbatim as blockers.
4. Render the current schematic with `kicad-cli sch export svg` or PDF and inspect it.
   Confirm the netlist has expected references and nets, every component has a valid
   footprint, and every physical IC pin is connected or explicitly NC. Verify bundled
   symbols/footprints resolve with KiCad CLI exports where applicable.
5. Run the project's canonical circuit verification suite when available, both
   targeted to this circuit and in its default mode. A skipped/missing target is not a
   pass; report unrelated full-suite failures separately. If no canonical suite
   exists, run focused checks and retain their commands and results in the circuit's
   verification directory. Never label focused checks as a canonical suite pass.
6. After any source, library, or schematic-support edit, repeat affected checks;
   previous evidence is stale. Simulation success does not clear ERC, and ERC success
   does not establish electrical safety, manufacturing readiness, or behavior beyond
   the simulated model.

In the response, include an `ERC report` with status, error and warning counts, and
each remaining finding's severity, rule, message, sheet, and reference/pin when
available. If none remain, state `No ERC violations.` Report validated versus
preliminary status, exact artifact paths, symbol/pin/footprint coverage, blockers,
commands actually run, and unsupported checks.

