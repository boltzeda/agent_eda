---
name: hardware-specialists
description: Choose and apply a focused hardware specialist guide.
version: 0.1.0
author: divya-shiva, Agent EDA
license: MIT
platforms: [linux, macos, windows]
metadata:
  agent-eda:
    tags: [electronics, hardware, pcb, power, embedded, validation]
    category: hardware
    related_skills: [intent-understanding, block-expansion, tscircuit-design, tscircuit-part-search, tscircuit-component-creation]
---

## BoltzPCB workflow integration

For circuit-design deliverables, follow the auto-loaded `eda-design-workflow`
instruction. Use this BoltzPCB skill and its native tools for this task.
Record evidence and explicit user skips as unverified in project notes and the
final report. This tool can also run independently for its own task.

# Hardware Specialists Skill

Use the specialist reference library for focused electronics design, review, debug, manufacturing, safety, and validation work. Select the guide by the task's deliverable, then apply its workflow alongside the relevant Agent EDA design skill and project instructions.

## When to Use

- A hardware task needs focused domain guidance beyond general circuit reasoning.
- The user asks for a design review, board debug, SI/PI, power, communications, safety, EMC, reliability, manufacturing, or validation work.
- A tscircuit design crosses a specialist area such as power conversion, high-speed layout, or package creation.

## Prerequisites

- Use `read_file` to read the selected role guide and `search_files` to find a guide by topic or role name.
- The source guides are under `references/skill_areas/`; the index is `references/skill_areas/README.md`.
- Use `web_search` and `web_extract` for current datasheets, standards, and manufacturer evidence when a role guide calls for external evidence.

## How to Run

1. Define the requested work product: design, implementation, review, troubleshooting, test plan, manufacturing assessment, or release decision.
2. Read the index and search the role guide names and domain descriptions for the closest match.
3. Load one primary specialist guide for the main work product. Load an adjacent guide when a concrete dependency crosses into that domain.
4. Apply the selected guide's workflow, evidence rules, and output format. Keep the user's stated constraints and the project-level safety and verification requirements in force.
5. For tscircuit deliverables, combine the specialist guidance with `tscircuit-design`; run `tscircuit-part-search` before parser fallback, and use `tscircuit-component-creation` only for artifacts still missing after search.
6. State which specialist guide informed the work and identify any evidence, assumptions, or required review that remains open.

## Quick Reference

| Domain directory | Choose it for |
|---|---|
| `analog_digital_embedded` | Analog, digital, mixed-signal, MCU, FPGA, SoC, low-power, or signal-chain design and review |
| `communications_and_control` | High-speed links, communication interfaces, control boards, industrial control, and chip/board co-design |
| `pcb_and_board` | PCB implementation, layout, SI/PI, footprint libraries, and board-level review |
| `power_and_energy` | Power trees, converters, motor drives, batteries, storage, and power design review |
| `safety_emc_reliability_manufacturing` | Safety, certification, EMC/EMI, ESD, reliability, failure analysis, DFM/DFA, and hardware process |
| `testing_and_validation` | Board debug, lab work, automated hardware tests, environmental tests, EVT/DVT, and validation reviews |

Use the index and targeted file search rather than loading every specialist guide. The collection includes overlapping roles; pick the guide whose deliverable matches the user's request and add an adjacent role only when the design has a real cross-domain dependency.

## Procedure

### 1. Match the guide to the deliverable

Search by task nouns and expected output, not by job title alone. Examples: use a footprint-library guide when the deliverable is a package library; use a PCB design-review guide when the deliverable is findings against an existing board; use a board-debug guide when the deliverable is a diagnosis plan for a failing prototype.

### 2. Read and apply the selected role guide

Read the whole selected guide. Carry forward its domain-specific assumptions, evidence requirements, review severities, verification methods, and output structure. Keep facts and engineering assumptions distinct, and use current primary sources for claims that depend on datasheets, standards, or vendor behavior.

### 3. Combine roles only across a concrete seam

Use one primary guide for the central deliverable. Add another guide when it owns a necessary neighboring question, such as power integrity for a high-speed board or safety review for an isolated converter. Keep recommendations coordinated and identify where each domain's responsibility ends.

### 4. Finish with evidence and open items

Report the selected guide, concrete findings or design decisions, the evidence behind them, verification performed, and unresolved questions or risks. For generated schematics, report supported ERC findings and explain each error in plain language, including whether it was fixed or remains unresolved.

## Pitfalls

- A role guide supplies a focused workflow; it does not replace the user's constraints, primary datasheets, current standards, or project instructions.
- A broad task does not require reading all role guides. Choose the closest primary guide and follow cross-domain references only when needed.
- Do not present estimates, remembered standards, or general role guidance as verified project evidence.
- A successful build or simulation does not by itself establish hardware safety, compliance, signal integrity, or manufacturability.

## Verification

- The selected guide matches the requested work product and specialty.
- Its applicable workflow and evidence requirements are reflected in the result.
- Any adjacent guide was chosen for a stated cross-domain dependency.
- Findings, checks, and unresolved risks are reported with clear evidence and status.
