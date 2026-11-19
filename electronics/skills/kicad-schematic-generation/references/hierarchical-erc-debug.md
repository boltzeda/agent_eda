# Hierarchical ERC debugging reference

## Trigger
Use when a generated hierarchical KiCad schematic emits multiple ERC classes at once: `pin_to_pin`, `hier_label_mismatch`, `power_pin_not_driven`, `pin_not_connected`, or custom-library warnings.

## Reproduction pattern

1. Read `verification/erc.json` and group by sheet, severity, and violation type.
2. Read the generated circuit JSON and list each child-sheet net with `(component, pin)` endpoints.
3. Compare output-to-output ERC item pairs against those logical net lists. In the isolated sensor-processing case, ERC attributed `U4` from the PT100 child to the current-output child even though the serialized nets kept `CURRENT_AMP_OUT`, `ADC_CURRENT`, `PT100_AMP_OUT`, and `ADC_PT100` separate. That indicates a hierarchy/placement attribution problem, not a confirmed logical short.
4. Do not edit the `.kicad_sch` directly. Fix `generate.py`, symbol sources, or library-table paths, regenerate with `force_regenerate=True`, then rerun ERC.

## Fix order

- Correct datasheet pin mapping first. The INA826 case initially mapped the gain pins and input pins incorrectly; correcting the physical map reduced the ERC count.
- Fix project-local custom-library paths after every regeneration. circuit-synth may rewrite custom entries to `${KICAD10_SYMBOL_DIR}`; use `${KIPRJMOD}/libraries/<lib>.kicad_sym` and verify the bundled files exist.
- Keep pure controller/sensor power domains from becoming accidental shared hierarchical signal ports. Passing `GND`/`+5V` through multiple child sheets caused label mismatch and duplicate-wire preflight failures.
- A passive external connector does not drive KiCad power-input pins. Use a correctly placed `PWR_FLAG` or real power source. If automatic placement routes the flag wire through the component body, do not retry blindly; change the source/placement topology.
- Only as an explicitly documented preliminary-symbol workaround may external supply pins be changed from `power_in` to `passive`. This removes ERC source checking and must not be described as power validation.

## Verification

After the final edit, run the project's canonical verifier when available. If it
does not cover the changed circuit, run a focused verifier saved under the
circuit's verification directory. Check Python compilation, current artifact
paths, custom-library paths, custom pin types, current ERC JSON, and affected
ngspice netlists. Report focused results separately from suite status. Preserve
the ERC count and remaining error classes; never call a reduction an ERC pass.
