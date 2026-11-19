---
name: eda-design-workflow
description: Guide circuit design, schematic, simulation, placement, and PCB work with independent Agent-EDA tools.
version: 2.0.0
author: BoltzPCB
---

# BoltzPCB design workflow

Apply this instruction to circuit-design deliverables. Match the work to the
user's requested scope: schematic, placement, or full PCB. Discussion and
standalone component, model, or library requests can use their tools directly.
The tools are independent; choose the relevant ones from the design evidence.
Their availability does not depend on a workflow project or stage record.

Use an existing or new project directory for artifacts. Read its AGENTS.md
and previous work. Preserve confirmed requirements and prior verified results.
For new projects, references/project-AGENTS-template.md is optional guidance.

## Design sequence

1. Understand the request. Identify the circuit function, input range,
   outputs and loads, operating conditions, constraints, and deliverable.
   Distinguish user requirements, sourced facts, and provisional assumptions.
   Ask only for a missing decision that materially changes the design.
2. Plan blocks and pins. Identify exact part variants and packages, block
   interfaces, pin numbers and functions, power, ground, control, unused pins,
   connectors, and intended nets. Save a component and connectivity plan.
3. Check libraries. Confirm a symbol and footprint for every physical
   component and verify pin-to-pad mappings and package geometry against
   evidence. Use the datasheet and library tools to find or generate missing
   assets. File existence alone does not establish correctness.
4. Generate and verify the schematic. Produce an editable .kicad_sch,
   inspect the rendering and connectivity against the pin plan, run ERC, and
   save the native file and review evidence. An SVG or successful generator
   exit alone is insufficient.
5. Simulate the generated circuit when in scope. Derive the SPICE netlist
   from the schematic's actual connectivity. Document model sources, pin
   mappings and substitutions. Compare relevant analyses with acceptance
   criteria and save the netlist and results. Report missing models or failed
   analyses as unverified.
6. Place when requested. Preprocess the schematic-derived netlist, resolve
   board constraints, place components, inspect the board and placement report,
   and present the placement for review.
7. Route when requested. Use the pcb_workflow tool's own approval contract:
   route the reviewed placement only after the user explicitly approves it.
   Check connectivity and native DRC; report partial routes and violations.

Use previously verified artifacts after checking their current contents. The
user may explicitly skip a stage or limit scope. Record what was skipped and
the resulting unverified items in the project notes and final report. A tool
failure is an unresolved issue, not a completed stage or a user skip. Continue
independent work when a tool is unavailable. Do not repeatedly seek approval
already given for the same placement.

## Preferred skills and tools

| Task | Skills | Direct tools |
|---|---|---|
| Requirements and blocks | intent-understanding, block-expansion, hardware-specialists | clarify when needed |
| Datasheets and libraries | component-validation, datasheet-to-component, kicad-library-finder, kicad-library-generator | component_datasheet_prepare, component_datasheet_parse, component_schema_validate, kicad_library_find, kicad_library_generate |
| Schematic | kicad-schematic-generation | kicad_schematic_setup, kicad_schematic_generate |
| Simulation and models | circuit-simulation, model-acquisition | Relevant simulator and model-acquisition tools |
| Placement and routing | pcb-workflow, hardware-specialists | pcb_workflow with separate preprocess, place, and route calls |

Prefer these BoltzPCB skills and native tools for their tasks. Use an imported
alternative if the user requests it or the preferred tool cannot meet the task;
explain the choice and verify the resulting artifact. Finish with artifact
paths, checks performed, explicit skips, unresolved issues, and the next step
within the requested scope.
