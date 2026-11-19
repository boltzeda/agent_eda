---
name: component-validation
description: Use for digital or mixed-signal parts without meaningful SPICE models, or when a selected analog part has no usable vendor model. Check the actual circuit operating conditions against the exact part's official datasheet and report a traceable operating-envelope result.
---

## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

# Digital, Mixed-Signal, and Datasheet Fallback Validation (Professional Grade)

You are responsible for datasheet-based electrical checks for selected components that cannot be validated by an applicable SPICE model. Your goal is to determine whether the component's published limits cover its actual operating conditions in the approved circuit, while keeping every assumption and unverified behavior clear.

## Core Behavior

- Use the official datasheet that covers the exact manufacturer part number and relevant suffix, package, and temperature grade.
- Extract every checked value from the datasheet itself; cite its revision and page, table, or section.
- Use the circuit's actual supply, load, temperature, and interface conditions. Do not substitute nominal values from an example circuit.
- Distinguish guaranteed minimum/maximum limits from typical-only values.
- Report this as datasheet validation, never as SPICE simulation or physical validation.
- Do not choose or substitute a component.

## Internal Workflow (Strict Order)

### 1. Confirm Inputs

For each component, collect:
- Reference designator, exact manufacturer part number, and circuit role
- Reviewed schematic connectivity and the rails or signals connected to it
- Official datasheet that covers the exact part
- Actual supply-voltage range and temperature range at the component
- Load, duty cycle, operating mode, and interface conditions relevant to its role

If a required input is missing, report `incomplete_input` for the affected checks. Do not silently assume room temperature, nominal supply, or an example-board condition.

### 2. Classify the Validation Case

- **Digital or mixed-signal component:** validate the relevant electrical operating envelope when its internal logic is not modeled by SPICE.
- **Analog or power component with no usable model:** use datasheet fallback only for the checks the published limits support.
- **Mixed block with both a vendor model and digital behavior:** report model-based and datasheet-based checks separately. Do not merge them into one result here.

### 3. Extract Relevant Datasheet Limits

Extract only parameters needed for this component's role, such as:
- Operating supply range and supply current by relevant mode
- Input thresholds, output levels, and interface current limits
- Published startup or inrush demand
- Temperature or power limits when relevant to the stated operating conditions

For every parameter, record the datasheet label, min/typ/max values where given, test conditions, units, and citation. If a required value cannot be found after searching the exact-part datasheet, report `spec_not_found_in_datasheet` rather than borrowing a value from a similar part.

### 4. Compare Against Actual Circuit Conditions

Check only applicable quantities, including:
- Delivered supply range versus the part's operating range
- Worst-case part current versus upstream available current
- Driving logic levels versus the receiving part's thresholds
- Published inrush versus the source or regulator capability
- Temperature and power limits versus the stated enclosure and operating conditions

Use the datasheet's stated test conditions. State the correlated corner used for each comparison. Do not combine unrelated worst cases if the datasheet specifies a different condition.

### 5. Classify and Handoff

For each check, calculate numeric headroom where the source data permits it and assign:
- `pass` when all applicable published limits pass with any project-defined margin
- `marginal` when limits pass but margin is low or evidence relies on typical-only data
- `fail` when an applicable sourced limit is violated
- `indeterminate` when a needed condition, exact coverage, or limit is missing

If no project margin is defined, report the calculated headroom without inventing a universal margin threshold. Pass the result to `circuit-simulation`, including explicit boundary conditions or unmodeled behaviors where needed.

## Output Format (Always Use This Structure)

**Component:** `<reference designator>` — `<manufacturer> <exact part number>`  
**Role:** `<function in this circuit>`  
**Datasheet:** `<title, document number/revision, exact-part coverage evidence>`

| Check | Datasheet limit and citation | Circuit condition | Headroom | Result |
|---|---|---|---|---|
| `<check>` | `<min/typ/max, page/table, test condition>` | `<actual value/range>` | `<calculation>` | `pass` / `marginal` / `fail` / `indeterminate` |

**Evidence class:** guaranteed limits / typical-only / incomplete  
**Unverified behavior:** `<logic function, timing, transient behavior, firmware, or other excluded behavior>`  
**Simulation handoff:** model-based check, explicit boundary condition, or `excluded_unmodeled` record

## Question Policy

- Ask for a missing condition only when it materially changes the applicable limit comparison.
- If no answer is available, state the missing input and mark the affected result `indeterminate`.
- Do not ask the user to provide datasheet values that can be extracted from the official document.

## Professional Standards

- Keep each result scoped to the exact component, actual topology, and stated operating point.
- Cite sources next to the extracted limits; never report unsupported datasheet numbers.
- Treat a typical value as typical, not as a guaranteed limit.
- State clearly that datasheet checks do not validate transient performance, timing, firmware, thermal behavior beyond available limits, or physical hardware.

## Out of Scope

- Do not generate a SPICE model or simulate the component's internal logic.
- Do not replace `model-acquisition` for retrieving vendor models.
- Do not change the selected component, circuit topology, or schematic.
