# Focused Verification Outside the Canonical Suite

Use when the project has no canonical suite for the changed artifact. Keep the
verifier and its evidence in `generated/<circuit>/verification/` (or the
project's established verification directory). Do not use temporary checks as
a substitute for the canonical suite when one exists.

## Required procedure

1. Create or update a focused verifier in the circuit's verification directory.
2. Check required root/child `.kicad_sch`, `.kicad_pro`, `.net`, generator, and library-table files are non-empty. Check simulation-result files when simulation is in scope.
3. Compile changed Python with the same project interpreter used for generation.
4. Parse changed JSON and validate expected PASS/FAIL fields and explicit fallback records.
5. Run every affected ngspice netlist in batch mode. Require a zero return code and a completion marker; a process exit alone is not enough if measurements are absent.
6. Render the current root schematic with `kicad-cli sch export svg`. Require at least one SVG output.
7. Read the current `erc.json`, count violations, and preserve the count in the report. ERC errors remain a hard schematic gate even if rendering and simulations pass.
8. Report `FOCUSED_VERIFY_PASS` only when all scoped checks above pass. This is
   a focused result, never “suite green”.
9. Keep commands and relevant outputs as verification evidence. Temporary
   render files may be removed after confirming the render succeeded.

## Status semantics

- `FOCUSED_VERIFY_PASS` means the changed artifacts execute, parse, render, and run their scoped checks.
- It does not mean ERC-clean, production-ready, fabrication-ready, or simulation-verified.
- If ERC has design errors, report `erc_status=BLOCKED` with the exact count and classify the schematic as preliminary/unverified.
- Behavioral/fallback simulations must list excluded IC behaviors and must not be reported as equivalent to validated vendor-macromodel simulation.
