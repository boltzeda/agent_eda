# Exact-part symbol and verification gate

Use this checklist for small regulator/converter schematics:

1. Confirm the exact selected MPN, package, and datasheet pin audit before generation.
2. Check the stock KiCad symbol and footprint once. A missing exact IC symbol is a design-generation blocker; do not substitute a connector or unrelated regulator symbol.
3. If a custom symbol is explicitly authorized, keep it local, label the result preliminary, and preserve the datasheet pin mapping and all configuration pins.
4. Run the canonical verifier targeted to the circuit. `SKIP: no schematic` is a failed/unreached gate, not success.
5. Run the canonical verifier in default workspace mode as well. Separate target-circuit status from unrelated legacy-circuit failures.
6. Report PySpice/ngspice validation separately from KiCad/ERC validation. An ideal behavioral stand-in validates only its declared boundary, not the real switching IC.

Typical honest status format:

- Simulation: passed for explicitly declared scoped boundary.
- KiCad generation: blocked or passed, with exact reason.
- Targeted canonical suite: passed, failed, or not reached.
- Full workspace suite: passed or failed, naming unrelated failures.
