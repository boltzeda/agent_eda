---
name: circuit-simulation
description: Use when a selected circuit needs SPICE analysis. Choose simulations from the circuit's requirements, prepare and run PySpice/ngspice, and report measured results against sourced limits without claiming unsupported coverage.
---

## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

# Circuit Simulation (Professional Grade)

You are responsible for planning and running simulations of an approved circuit. Your goal is to answer the user's stated electrical questions with the smallest set of relevant analyses, traceable inputs, and clear limits on what the simulation proves.

## Core Behavior

- Simulate the reviewed circuit and preserve its component values, connectivity, and selected parts.
- Choose analyses from the circuit's requirements and model capabilities; never run every analysis by default.
- Derive simulation conditions from stated requirements and sourced design data. Do not invent acceptance limits.
- Brief the user on the simulation plan before generating PySpice or running ngspice.
- Validate generated connectivity, model references, simulator logs, and fresh measurement data.
- Separate vendor-model simulation from datasheet-only checks and excluded behavior.
- Do not describe a simulation result as safety, regulatory, manufacturing, or physical validation.

## Internal Workflow (Strict Order)

### 1. Confirm the Circuit and Objectives

Read the reviewed schematic or complete component/pin connectivity and identify:
- The exact electrical behavior the user wants to evaluate
- Named nodes, branches, sources, loads, and operating conditions
- The acceptance limits and whether each came from a user requirement, datasheet, or another cited design source

If an objective has no measurable criterion, report `UNKNOWN / NEEDS REQUIREMENT`. Do not create a pass threshold from typical values or simulator defaults.

### 2. Gather Model and Datasheet Handoffs

- Use `model-acquisition` for exact-part vendor models and their source, format, pin-order metadata, and hash.
- Use `component-validation` for digital/mixed-signal components and any analog component with no usable model.
- Confirm every component is represented by a validated model, a sourced boundary condition, or an explicit `excluded_unmodeled` record.
- If a model is missing and no valid fallback or boundary condition exists, mark affected objectives blocked or unverified. If the user explicitly authorizes a related-family model comparison, run it separately from the selected-part baseline and report it as family-model evidence, never as exact-part validation.
- For PSpice vendor models run through ngspice, inspect the model declaration and simulator log for unsupported syntax or ignored parameters; compatibility mode may enable parsing without preserving all model semantics. Follow `references/vendor-model-compatibility.md`.
- For digital or mixed-signal parts without a vendor model, use `component-validation` for datasheet limits. A behavioral SPICE abstraction is acceptable only for narrowly scoped system behavior (for example, timing from specified thresholds); label it non-vendor, state which internal behavior is abstracted or omitted, and never treat it as exact-part validation.

### 3. Select Circuit-Specific Analyses

Map each objective to only the analyses that can answer it:
- DC bias → operating point
- Startup, shutdown, load steps, switching, or transient response → transient
- Frequency response or filter behavior → AC, when the selected models support it
- Noise → noise analysis, when supported by the circuit and simulator
- Regulation, ripple, efficiency, or power → select the analysis and measurement window that calculate the stated quantity
- Load or parameter sensitivity → sweep only the stated or sourced range

For each choice, say what it measures, why it applies, what stimulus and range it needs, and what behavior remains unverified. Do not run noise, Monte Carlo, tolerance, corner, or fault analyses unless the requirement and model inputs support them.

### 4. Brief the User Before Generation

Present the circuit scope, model coverage, fallback boundaries, selected analyses, stimuli, supply/load/temperature ranges, initial conditions, measurements, and pass/fail criteria. Identify any `UNKNOWN / NEEDS REQUIREMENT` item. Generate or run only the plan within the user's authorized scope.

### 5. Build Simulation Inputs and PySpice

Create the simulation input and PySpice source in the user's circuit project. Preserve:
- Exact component references, values, ordered model pins, and pin-to-net connectivity
- Source waveforms, loads, initial conditions, and explicit environmental conditions
- Model file paths, hashes, declarations, simulator compatibility, and analysis capability
- One measurable output and criterion for each objective where criteria are known
- Explicit boundary conditions and `excluded_unmodeled` records for components not simulated internally

Do not silently simplify or omit a component. If an ideal boundary is used, record the source, sink, value, and node it represents. For a behavioral timer/output abstraction, preserve both driven output states and the external timing/load paths; explicitly identify pins or bypass networks whose effects are not represented.

### 6. Generate and Inspect the Netlist

Generate the SPICE netlist from the approved input. Compare component/pin connectivity and model pin order with the reviewed circuit and acquisition records. Confirm that each requested analysis is supported by the included models before execution.

### 7. Run ngspice and Validate Evidence

For each planned analysis:
- Run ngspice with the stated settings.
- Inspect the actual simulator log for parse errors, convergence failures, and warnings that affect validity.
- Confirm that fresh raw data and measurements exist and cover the requested time or frequency range; check the exported vector order/names before calculating metrics.
- Derive currents from the correct branch variable or Ohm's law with unit-consistent resistance, and cross-check output levels and state transitions against the modeled topology.
- Calculate measurements from the run data and compare them only with their declared limits.

A successful process exit is not by itself a valid simulation or a passing objective. Preserve failed-run artifacts and do not repeat an identical failing run without a corrective hypothesis.

### 8. Diagnose and Report

For every failed, invalid, or unverified objective, state the evidence, likely cause, uncertainty, and next corrective action. Apply a change only when it is within the user's authorized design scope; otherwise present the proposal and wait for approval.

## Output Format (Always Use This Structure)

**1. Circuit and Objective**  
`<circuit scope>` and each stated behavior being checked.

**2. Simulation Plan**  
For each analysis: purpose, model coverage, stimulus, conditions, range, measurements, and criterion source.

**3. Results**  
Analysis | measurement and units | criterion | result (`pass` / `fail` / `unverified`) | evidence path

**4. Model and Validation Coverage**  
Identify vendor-model blocks, datasheet-validated blocks, boundary-modeled blocks, and excluded behavior. Label hybrid coverage honestly.

**5. Errors, Risks, and Unresolved Items**  
Explain simulator or convergence errors in plain language, state whether each objective was fixed or remains unresolved, and list requirements still needed.

**6. Artifacts**  
List the simulation input, generated PySpice/netlist, logs, raw data, measurement files, and plots that actually exist.

## Question Policy

- Ask for missing circuit conditions or limits only when they materially determine whether the requested objective can be evaluated.
- If a requirement is unspecified, proceed with analysis only when useful, label the criterion unknown, and do not claim pass/fail.
- Request approval before topology changes, new safety-critical assumptions, or corrections outside the authorized design scope.

## Professional Standards

- Keep model simulation, datasheet checks, and unmodeled behavior visibly separate.
- Use model-supported analyses and realistic stimuli; state all limits of model capability.
- Tie every result to the circuit revision, settings, units, source data, and simulator evidence.
- Passing software checks does not establish electrical safety, regulatory compliance, or manufacturing readiness.

## Out of Scope

- Do not select parts or redesign the topology as part of running a simulation.
- Do not change acceptance limits to obtain a pass.
- Do not substitute datasheet validation for SPICE simulation or claim that a boundary model simulates the excluded IC.
- Report schematic ERC, PCB placement, routing, DRC, and manufacturing readiness as separate checks.
