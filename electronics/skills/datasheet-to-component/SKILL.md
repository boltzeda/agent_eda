---
name: datasheet-to-component
description: "Build a sourced component record from a datasheet."
---

## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

# Datasheet to Component Schema

Use the CLI tools `component_datasheet_prepare`, `component_datasheet_parse`,
and `component_schema_validate` to turn an exact component datasheet into a
traceable, CAD-neutral JSON record.
The schema is an intermediate data contract, not a library-file format.

## Inputs

- Exact manufacturer name and exact manufacturer part number (MPN).
- Either a user-provided datasheet PDF or a datasheet URL selected from the
  manufacturer's official product/document site. Use an authorized distributor
  only as a clearly identified mirror when the manufacturer source is not
  available.
- Output directory for the PDF, manifest, parser artifacts, and normalized JSON.

If the user provides only a product request without an exact MPN, resolve the
part identity and package first; ask a concise clarification when multiple
common variants would change pinout, body, or footprint. State the selected
manufacturer, exact MPN, and package before sourcing evidence. Do not silently
substitute a related part or package variant.

## Procedure

1. Obtain the selected datasheet URL or PDF. Call
   `component_datasheet_prepare` with manufacturer, exact MPN, source type,
   output directory, and exactly one of `pdf_path` or `pdf_url`. The tool validates that the input opens as a PDF, records
   page count and SHA-256, and writes a manifest.
3. Inspect the manifest. Confirm the document identifies the exact manufacturer
   and MPN and that the URL/source is appropriate. A text-layer match is only
   supporting evidence; if identity is ambiguous, stop and report the ambiguity.
4. Call `component_datasheet_parse` with the manifest path. Read the generated
   Markdown and content-list JSON, prioritizing pin tables, package drawings,
   recommended land patterns, and their page numbers. Keep parser artifacts as
   evidence; do not treat OCR output as authoritative when the source drawing
   or table contradicts it.
5. Interpret the extracted evidence and write
   `normalized_component.json` using
   `component_datasheet_workflow/schema/normalized_component.schema.json`.
   Include every physical pin or terminal, preserving the datasheet numbering
   and names. Record generic electrical meaning only when supported. Record
   body measurements and land-pattern pad geometry only when the datasheet
   provides enough evidence. Cite page numbers and a short source excerpt for
   each pin and each land-pattern definition.
6. Preserve source URL/type, datasheet revision/date when present, and the
   manifest SHA-256 in the JSON. Put missing dimensions, ambiguous pin meaning,
   OCR conflicts, and unverified assumptions in `review.unresolved_items`.
   Do not turn a guess into a measured value; use null or omit optional data.
7. Perform the evidence-based content review described below. Update
   `review.unresolved_items` for conflicts, unsupported claims, or facts that
   could not be checked.
8. Call `component_schema_validate` with the normalized JSON file path as
   `schema_json` and the manifest path. Fix
   structural, identity, hash, or out-of-range page errors. This check does not
   establish engineering correctness.
9. Report the manufacturer, MPN, datasheet source and revision, page count,
   SHA-256, JSON path, parser artifact paths, validation report path, and
   unresolved review items.

## Evidence-based content review

After drafting `normalized_component.json`, review every engineering claim
against the cited page in the original PDF. This is a semantic evidence review;
the schema validator only checks structure and provenance links.

1. Review the component identity and declared pin count, then every pin's
   number, name, description, and electrical type. Also review each populated
   body dimension and every land-pattern pad's number, shape, coordinates,
   dimensions, drill diameter, and plating value.
2. Open and inspect the cited original PDF page for each claim. For pin tables,
   compare the table entry directly. For package and land-pattern values, inspect
   the drawing, its dimension annotations, and any accompanying notes or tables.
   Use MinerU Markdown and content-list data to find the relevant region, but do
   not treat those extracted files alone as verification of the source page.
3. Assign each reviewed claim one status: `supported`, `conflicting`, or
   `not found`. Use `supported` only when the cited source page directly supports
   the value or the reported derivation. For a derived pad coordinate, show the
   source dimensions and calculation. Use `conflicting` when the page disagrees
   with the JSON. Use `not found` when the cited pages do not establish the claim
   or the original page could not be inspected; state which case applies.
4. Write a `component_validation_report.md` beside the normalized JSON. Include
   the datasheet SHA-256, a result for every claim, its JSON field or pin/pad
   identifier, status, cited page, and the short source evidence or visual
   observation used. For conflicts, state the source value and JSON value. For
   `not found`, explain what evidence is missing or why inspection was not
   possible.
5. Add every `conflicting` and `not found` claim to
   `review.unresolved_items`. Correct the JSON when the datasheet clearly
   resolves a conflict, then rerun the content review and schema validator. Do
   not describe the component data as verified while unresolved claims remain.

The agent performing this review is an evidence reviewer, not an independent
physical measurement instrument. If it cannot view the original PDF page, it
must not mark a claim `supported` based only on MinerU output. Report that claim
as `not found` with the inspection limitation, and leave it for human review.

## Output boundary

This skill ends with the normalized JSON and its evidence. Existing library
search and reuse are handled by another skill. Symbol/footprint generation,
CAD export, and library registration are downstream steps. Keep the schema
independent of any CAD vendor: use generic pin semantics, millimeter package
dimensions, relative pad coordinates, and datasheet citations. Never add
vendor-specific library IDs, symbol types, footprint names, or file paths as
requirements of this schema.
