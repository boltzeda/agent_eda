---
name: kicad-library-finder
description: "Find symbols and footprints in installed KiCad libraries."
---

## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

# KiCad Library Finder

Use `kicad_library_find` to check whether KiCad already has a symbol or
footprint for a component before considering generation. Prefer the exact
normalized component record (`schema_json`) when available; otherwise provide
`symbol_name`. For a named part family without an exact MPN/package, resolve
those first because similarly named variants may not be interchangeable.
Optional `package_name`, `footprint_name`, `symbols_dir`, and `footprints_dir`
can narrow the search or select a nonstandard KiCad installation.

The symbol name lookup is exact. Treat a package-name footprint match as a
candidate, then compare its pad numbering, geometry, and orientation with the
datasheet. Report matched library paths and any mismatch or uncertainty.
