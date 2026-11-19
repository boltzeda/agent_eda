# Vendor SPICE Model Compatibility and Related-Part Comparisons

Use this procedure when an official vendor model is distributed in a simulator dialect different from the available simulator, or when a user explicitly authorizes a comparison using a related family part after the exact-part model is unavailable.

## Procedure

1. Request the model for the exact selected manufacturer part number and intended simulator format. Preserve the acquisition status, official source, download URL, resource title, coverage classification, model path, and SHA-256.
2. If exact coverage is unavailable, do not silently substitute. A related-part model may be used only as a separately labeled comparison when the user authorizes it; retain the existing selected-part or behavioral baseline for side-by-side comparison.
3. Inspect the unmodified model file. Read its applicability statement and top-level `.SUBCKT` declaration, then map each declared node to the reviewed schematic pin using the exact package pinout. Do not infer model pin order from the symbol or subcircuit name.
4. Copy the downloaded file byte-for-byte into the project when needed. Verify the copy's SHA-256 matches the acquired file; never edit the vendor file to make it parse.
5. For a PSpice model parsed by ngspice, first run it unchanged and inspect the complete log. If the failure is PSpice syntax parsing, test ngspice's PSpice compatibility mode using a project-local `.spiceinit` containing `set ngbehavior=ps`, and launch ngspice with that project directory as its working directory. This is a syntax-compatibility aid, not proof of semantic equivalence.
6. Check the successful-run log for warnings about ignored or unsupported parameters, devices, or model constructs. If any could affect the measured behavior, state the affected results are qualified/approximate; do not present a successful process exit as an unqualified model pass.
7. Preserve the source model, simulation netlist, compatibility configuration, simulator log, raw waveform, and measurement derivation together. Report the actual model coverage and unsupported behavior beside every comparison.

## Pitfalls

- Do not call a family-level model an exact-part model merely because the product name appears in the file; the vendor's applicability declaration and acquisition coverage determine the claim.
- Do not change the original vendor model to eliminate compatibility warnings; edits invalidate the acquired hash and can change its behavior without vendor provenance.
- Do not hide ignored-parameter warnings when reporting a run; unsupported transistor or behavioral parameters can alter output drive or dynamic response even when timing appears plausible.
- Do not compare a family model against a different baseline with changed passives, supply, load, initial conditions, measurement thresholds, or transient window; otherwise observed deltas are not attributable to model differences.
