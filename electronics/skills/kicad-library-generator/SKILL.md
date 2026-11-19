---
name: kicad-library-generator
description: "Generate KiCad libraries from reviewed component data."
---

## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

# KiCad Library Generator

Use `kicad_library_generate` only when a normalized component schema has
been reviewed and the user requests library generation. Resolve the exact
orderable variant and every requested package before building: a family name
can span fixed-output options and package-specific MPNs, while one symbol may
serve multiple footprints. Once the user has specified the electrical variant
and package, do not ask them to approve a datasheet-defined implementation
choice; consult the manufacturer source and proceed. Ask only when a material
ambiguity remains that the source cannot resolve. If the user names multiple acceptable packages
without choosing one, treat that as a request for all named package options
unless they say otherwise; identify the corresponding exact MPNs and build a
separate reviewed package record for each footprint. Provide `schema_json`
(the path to the normalized component JSON file) and `library_root`; include
`manifest_path` when provenance validation is available. Follow the
`datasheet-to-component` evidence workflow for exact identity, pin mapping,
and package geometry; do not generate from a family name or an unreviewed
package assumption. When the exact package is identified but its datasheet lacks
a land pattern, inspect the original datasheet mechanical drawing and search
installed KiCad libraries for a matching standard footprint. Verify the
package outline, lead/pad pitch, numbering, orientation, and thermal/tab pad
against the drawing before reuse. An OCR/text extraction can locate relevant
pages but does not verify package geometry; render and inspect the cited PDF
pages when dimensions or land patterns affect generation. Clearly distinguish
reusing a verified standard footprint from generating a new one; a name or
package-family match alone does not establish exact geometry. If no verified
footprint is available, keep footprint generation blocked until the user
explicitly accepts a draft. If preparation, parsing, or validation fails,
report the actual blocker and do not claim the library was generated.

The generator requires `kicad-cli` for preview validation. Review its
generation report and production gate, then independently compare the generated
pad numbers, pin-1 orientation, tab assignment, pitch, and land dimensions to
the inspected manufacturer drawing; a passing automated gate does not confirm
orientation or package-specific pad mapping. Preserve blocked or draft status
and unresolved review items; do not report those artifacts as production-ready.
Use `allow_draft_footprint` only when the user explicitly accepts a draft.
Report generated file paths, validation results, and remaining review items.
The tool does not register libraries globally.
