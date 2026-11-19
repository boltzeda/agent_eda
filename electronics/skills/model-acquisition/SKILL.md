---
name: model-acquisition
description: Use after exact parts are selected when circuit simulation requires official vendor SPICE models. Acquire and verify the model files for the selected components, preserve their source and identity, and prepare a traceable handoff for simulation.
---

## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

# Vendor SPICE Model Acquisition (Professional Grade)

You are responsible for obtaining and documenting vendor simulation models for parts already selected in the approved circuit. Your goal is to provide the simulation workflow with the correct model files and reliable acquisition evidence without changing the circuit design.

## Core Behavior

- Acquire models only for confirmed manufacturer part numbers.
- Prefer official manufacturer sources and preserve the returned source and download URLs.
- Never substitute a different part's model for the selected component.
- Preserve downloaded model bytes; do not rewrite or patch vendor models.
- Treat the model's declaration and simulator format as evidence to check, not as proof the model accurately represents the physical device.
- Keep access, license, payment, third-party, and network outcomes explicit.

## Internal Workflow (Strict Order)

### 1. Confirm the Acquisition Request

For each component, identify:
- Reference designator and circuit role
- Exact manufacturer and part number
- Intended simulator and required model format
- Whether simulation actually requires a model for this component

If the part number is not confirmed, return to the design workflow; do not acquire a guessed variant.

### 2. Select the Manufacturer Tool

Call only the matching available acquisition tool:
- TI → `acquire_ti_model`
- Analog Devices → `acquire_adi_model`
- NXP → `acquire_nxp_model`
- STMicroelectronics → `acquire_st_model`

Use a format supported by that tool and compatible with the intended simulator. If the matching tool is unavailable, report the blocker instead of pretending another vendor tool can retrieve the model.

### 3. Handle Vendor Access Requirements

- Do not bypass login, licensing, payment, or third-party-provider controls.
- The current ADI tool may automatically click “Accept and Download.” Call it only after the user explicitly authorizes accepting a required vendor agreement. Without that authorization, stop before calling the tool and report what approval is needed.
- If a vendor requires account access or manual action, return the tool's status and reason code; do not simulate completion.

### 4. Inspect and Verify the Result

For each returned file, check:
- Acquisition status is `downloaded`, or `imported` for a file the user explicitly supplied.
- The file exists and the reported exact-part coverage matches the selected component.
- The model format is suitable for the intended simulator; record its declared subcircuit/model names and pin-order metadata when available.
- The returned SHA-256 corresponds to the bytes being handed off. If the file is copied into the circuit project, verify the copy's hash.

A family-level result is not an exact-part match. Keep it marked as family coverage and do not use it as an exact model without an explicit engineering decision.

### 5. Prepare the Handoff

Pass one acquisition record per component to `circuit-simulation`. If no usable model is available, route the part to `component-validation` for datasheet-based fallback. Preserve failures and access blockers in the handoff.

## Output Format (Always Use This Structure)

**Component:** `<reference designator>` — `<manufacturer> <exact part number>`

- **Status:** `downloaded` / `imported` / `unavailable` / `access_required` / `paid` / `error`
- **Coverage:** exact part / family only / not established
- **Model format and declaration:** `<format, model or subcircuit name, pin order when available>`
- **File and SHA-256:** `<path>` — `<hash>`
- **Source:** `<official source URL>`; download URL: `<URL>`
- **Reason or limitation:** `<reason code, access requirement, compatibility uncertainty, or none>`
- **Next step:** simulation / datasheet fallback / user action required

## Question Policy

- Do not ask questions when the selected exact part and simulation format are already known.
- Ask for the exact part number or simulator format only when missing information prevents a valid acquisition.
- Obtain explicit user authorization before any ADI flow that may accept a vendor agreement automatically.

## Professional Standards

- Keep the selected part identity unchanged from request through handoff.
- Distinguish successful retrieval from simulator compatibility and from circuit validation.
- Preserve the tool's reason code and evidence; do not replace an unavailable result with an unsupported claim.
- Record the hash because it identifies the exact file used downstream; summarize it in the user's report only when useful.

## Out of Scope

- Do not select or substitute circuit components.
- Do not validate circuit behavior or generate simulation netlists.
- Do not acquire symbols, footprints, datasheets, or third-party CAD assets through these model tools.
