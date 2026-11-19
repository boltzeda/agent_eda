# Preliminary custom-symbol ERC recipe

Use this only after the user authorizes a one-time local preliminary symbol.

1. Preserve the exact datasheet pin numbers and pin names in the custom `.kicad_sym`; label the symbol/value as preliminary.
2. Build a local symbol directory containing the custom symbol plus every stock library the generated schematic uses, especially `Device`, `Connector_Generic`, and `power`. Refresh the directory before each regeneration so a stale cached symbol cannot survive a pin-type edit.
3. Generate the schematic, then run `kicad-cli sch erc` and inspect the actual report. Do not rely on the generator's exit code alone.
4. Fix genuine defects before relaxing preliminary electrical types:
   - missing connections: correct the net declaration or physical routing;
   - power-input errors: provide a real source or a documented `PWR_FLAG` on the supplied power net;
   - output-to-output errors: do not tie unrelated gate-driver outputs together; use separate named nets;
   - control/status labels: keep them explicit and report single-pin label warnings.
5. If the external power stage is intentionally abstracted, passive pin types may be used for unresolved power-stage/control pins only when the handoff states that this is an ERC accommodation, not a datasheet claim.
6. Run the canonical project verifier after the final regeneration. Required evidence is: ERC error count, warning count, schematic render result, and the exact command output. A pass with warnings is not fabrication-ready; report the warnings and preliminary status.

Typical failure pattern: editing the source custom symbol but leaving an older copy under the local `symbols/` directory. Delete/recreate or overwrite the local copy before importing the generator; otherwise the generated schematic embeds the old pin electrical types.
