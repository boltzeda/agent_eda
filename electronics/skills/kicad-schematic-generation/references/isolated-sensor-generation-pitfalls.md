# Isolated sensor schematic generation pitfalls

Session-derived checklist for provisional isolated analog-input schematics:

- A generated `.kicad_sch` and successful SVG render do not establish electrical correctness. Run fresh ERC after every source edit and report the error count.
- A configurable current-input channel must use the burden resistor for the selected sensor's actual maximum secondary current. For the DVL1000 baseline, 24 ohm maps +/-75 mA to +/-1.8 V; a previously emitted 5 ohm value was not traceable to that baseline and had to be corrected.
- Keep the LF1005 configuration distinct: its nominal secondary current is about +/-200 mA and its measuring-resistance constraint requires a different burden population (10 ohm baseline), not the DVL resistor.
- A Pt100 -50 to +150 C block needs an explicit excitation and Kelvin wiring plan. A connector label such as `PT100_3WIRE` is not equivalent to a verified 4-wire RTD connection.
- If the controller ADC MPN is unavailable, an ADC connector is only a preliminary boundary, not an implemented ADC. Preserve the limitation in the handoff.
- In hierarchical generation, allocate reference designators globally and compare ERC output-to-output reports against the serialized JSON/netlist before deciding that the circuit is electrically shorted. Distinct logical nets attributed to the wrong child sheet are a generator/placement blocker, not an ERC-clean result.
- Non-stock symbols must be bundled into the project-local libraries directory and tables repaired after every regeneration; otherwise ERC library warnings can obscure the real electrical violations.
