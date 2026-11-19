---
name: power-electronics
description: "Route power-tree, converter, motor-drive, BMS, and storage hardware tasks to the correct power specialist. Use for broad power electronics requests, including when Agent-EDA would otherwise try to load a generic power-electronics skill."
---

# Power Electronics Skill Router

Use this domain skill when the request is broad or crosses several roles. Select
one primary specialist below and at most two supporting specialists. Load skills
by the exact lowercase slug; never invent a category name.

## Available specialists

- `bms-hardware-engineer` — BMS Hardware Engineer
- `energy-storage-hardware-engineer` — Energy Storage Hardware Engineer
- `motor-drive-hardware-engineer` — Motor Drive Hardware Engineer
- `power-electronics-engineer` — Power Electronics Engineer
- `power-hardware-engineer` — Power Hardware Engineer


## Workflow

1. Identify the hardware artifact, lifecycle phase, and dominant risk.
2. Load the closest primary specialist by its exact slug.
3. Add no more than two supporting specialists for genuine cross-domain risks.
4. Apply the active SOUL lifecycle gates and preserve evidence boundaries.
5. If no listed role fits, continue with this router's domain guidance and state
   the uncovered specialty instead of calling a nonexistent skill.
