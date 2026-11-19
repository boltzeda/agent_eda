---
name: freerouting-autoroute
description: Explicit alternative; prefer pcb-workflow routing.
  Run FreeRouting autorouter directly through backend Python calls (bypassing the KiCad MCP tool layer).
  Use whenever a user wants to: autoroute a PCB, run freerouting on a board, auto-route traces,
  export a board to DSN format, import an SES routing result, check freerouting prerequisites,
  or generate routed PCB layouts. Also triggers on: "route my PCB", "autoroute the board",
  "run freerouting", "export DSN", "import SES", "check freerouting setup",
  "auto-route my layout", "perform PCB routing".
---

## BoltzPCB workflow integration

This is an alternative implementation. Prefer the task-specific BoltzPCB skills
listed in `eda-design-workflow`. Use this alternative only for an explicit user
choice or a demonstrated capability gap, explain the switch, and retain the
project evidence, checks and recorded user skips. Never use it to bypass a
failed prerequisite or silently replace the preferred pipeline.

# FreeRouting Autoroute

Run the FreeRouting autorouter on a KiCad PCB directly through backend Python calls.
Handles the full DSN export -> FreeRouting execution -> SES import pipeline, as well as
individual steps for manual control.

---

## Prerequisites

Before running any FreeRouting operation, verify these exist:

1. **KiCad Python API (`pcbnew`)** - Must be importable. Ships with KiCad installation.
2. **FreeRouting JAR** - Download from https://github.com/freerouting/freerouting/releases
   - Default location: `~/.kicad-mcp/freerouting.jar`
   - Override with `FREEROUTING_JAR` environment variable
   - **Version compatibility**: v2.0.1 requires Java 21, v2.2.x requires Java 25
   - Download v2.0.1 for Java 21: `curl -L -o ~/.kicad-mcp/freerouting.jar https://github.com/freerouting/freerouting/releases/download/v2.0.1/freerouting-2.0.1.jar`
3. **Java 21+** (Option A) OR **Docker/Podman** (Option B)
   - Java: `sudo apt install openjdk-21-jre`
   - Docker: `docker pull eclipse-temurin:21-jre`
   - **Important**: Match JAR version to Java version (v2.0.1 = Java 21, v2.1+ = Java 25)
4. **A valid `.kicad_pcb` file** with components placed and netlist loaded

## Backend Classes & Functions

The implementation lives in `python/commands/freerouting.py`:

```python
# IMPORTANT: Import the module directly to avoid dependency issues from __init__.py
import importlib.util
spec = importlib.util.spec_from_file_location(
    'freerouting',
    '/path/to/KiCAD-MCP-Server/python/commands/freerouting.py'
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
FreeroutingCommands = mod.FreeroutingCommands

# Constructor takes a pcbnew board object:
fr = FreeroutingCommands(board)

# Four available methods:
fr.check_freerouting(params)   # Verify prerequisites
fr.autoroute(params)           # Full automated pipeline
fr.export_dsn(params)          # Export board to DSN only
fr.import_ses(params)          # Import SES routing result only
```

## Step-by-Step Instructions

### Step 1: Load the Board

```python
import pcbnew
board = pcbnew.LoadBoard("/path/to/board.kicad_pcb")
```

The board MUST have components placed and a valid netlist. FreeRouting routes based on
connectivity defined in the netlist - no nets means nothing to route.

### Step 2: Check Prerequisites (Recommended First Step)

```python
fr = FreeroutingCommands(board)
result = fr.check_freerouting({"freeroutingJar": "/path/to/freerouting.jar"})
# result["ready"] == True means good to go
# result["execution_mode"] == "direct" (Java) or "docker" (container)
```

If `ready` is False, diagnose and fix:
- `freerouting.jar` not found: download it to `~/.kicad-mcp/`
- Java < 21: upgrade Java or install Docker as fallback
- No Java/Docker: install one of them

### Step 3A: Full Automated Autorouting

```python
result = fr.autoroute({
    "boardPath": "/path/to/board.kicad_pcb",
    "freeroutingJar": "/path/to/freerouting.jar",  # optional
    "maxPasses": 20,   # optional, default 20
    "timeout": 300,     # optional, default 300 seconds
})
```

This runs the complete 4-step pipeline:
1. Export board to Specctra DSN format via `pcbnew.ExportSpecctraDSN(board, dsn_path)`
2. Run FreeRouting CLI: `java -jar freerouting.jar -de <dsn> -do <ses> -mp <passes>`
3. Import routed SES via `pcbnew.ImportSpecctraSES(board, ses_path)`
4. Save board via `board.Save(board_path)`

### Step 3B: Manual Step-by-Step Workflow

For more control, run each step individually:

```python
# Export DSN only
result = fr.export_dsn({
    "boardPath": "/path/to/board.kicad_pcb",
    "outputPath": "/path/to/output.dsn",  # optional, defaults to same dir as board
})

# ... user can inspect DSN, run FreeRouting externally, etc. ...

# Import SES only
result = fr.import_ses({
    "sesPath": "/path/to/board.ses",
    "boardPath": "/path/to/board.kicad_pcb",  # optional if board already loaded
})
```

### Step 4: Verify Results

After autorouting, check the result:

```python
if result["success"]:
    tracks = result["board_stats"]["tracks"]
    vias = result["board_stats"]["vias"]
    elapsed = result["elapsed_seconds"]
    mode = result["mode"]  # "direct" or "docker"
```

## Input Parameters Reference

### `autoroute` params

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `boardPath` | string | No* | Board's own path | Path to `.kicad_pcb` file |
| `freeroutingJar` | string | No | `~/.kicad-mcp/freerouting.jar` | Path to FreeRouting JAR |
| `maxPasses` | int | No | 20 | Max routing passes (higher = more thorough) |
| `timeout` | int | No | 300 | Timeout in seconds |

*Required if board was not loaded from a file.

### `export_dsn` params

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `boardPath` | string | No | Board's own path | Path to `.kicad_pcb` file |
| `outputPath` | string | No | `<board_stem>.dsn` | Output DSN file path |

### `import_ses` params

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `sesPath` | string | Yes | - | Path to `.ses` file to import |
| `boardPath` | string | No | Board's own path | Path to `.kicad_pcb` file |

## File Interchange Format

```
KiCad (.kicad_pcb)
    |
    | pcbnew.ExportSpecctraDSN()
    v
DSN file (.dsn)  -- Contains: footprints, pads, netlist, design rules, layer stack, board outline
    |
    | java -jar freerouting.jar -de <dsn> -do <ses> -mp <passes>
    v
SES file (.ses)  -- Contains: routed track geometry, via placements
    |
    | pcbnew.ImportSpecctraSES()
    v
KiCad (.kicad_pcb)  -- Board saved with routes applied
```

- **DSN** = Specctra Design Exchange format (KiCad exports it, FreeRouting reads it as input)
- **SES** = Specctra Session file (FreeRouting writes it as output, KiCad imports it)

## Error Diagnosis

| Error | Cause | Fix |
|-------|-------|-----|
| "pcbnew not available" | KiCad Python API not installed | Install KiCad with Python bindings |
| "No board is loaded" | Board object is None | Load a board with `pcbnew.LoadBoard()` first |
| "Freerouting JAR not found" | JAR not at expected path | Download JAR to `~/.kicad-mcp/` or set `FREEROUTING_JAR` env var |
| "Neither Java 21+ nor Docker found" | No runtime available | Install Java 21+ or Docker |
| "Java found but version < 21" | Old Java version | Upgrade to Java 21+ or install Docker as fallback |
| `UnsupportedClassVersionError` (class file version 69) | FreeRouting JAR requires newer Java | Use FreeRouting v2.0.1 for Java 21, or upgrade Java to match JAR version |
| "Freerouting timed out" | Complex board, insufficient time | Increase `timeout` param or reduce board complexity |
| "Freerouting did not produce SES output" | Routing failed completely | Check DSN validity, increase `maxPasses`, verify design rules |
| "DSN file was not created" / Export returns False | Board has complex features incompatible with Specctra export | Try a different board, or verify board has valid netlist and components |
| "Freerouting exited with code 1" | Java version mismatch or invalid DSN | Check Java version matches JAR version, verify DSN was created |

## Rules & Guardrails

- ALWAYS run `check_freerouting` before the first autoroute attempt in a session
- ALWAYS verify the board has components placed and a valid netlist before routing
- NEVER run FreeRouting on an empty board (no components) - it will produce no results
- If autorouting fails, diagnose the specific error from the table above before retrying
- For complex boards, start with a higher `maxPasses` (30-50) and longer `timeout` (600s)
- After successful routing, consider running DRC (Design Rule Check) to verify results
- The board file is overwritten on save - ensure backups exist for important designs

## Example: Complete Workflow (Tested & Verified)

```python
import pcbnew
import importlib.util
import json, os, shutil

# Direct import of freerouting module (avoids __init__.py dependency issues)
spec = importlib.util.spec_from_file_location(
    'freerouting',
    '/path/to/KiCAD-MCP-Server/python/commands/freerouting.py'
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
FreeroutingCommands = mod.FreeroutingCommands

# Load board (use a copy to preserve original)
src = '/path/to/board.kicad_pcb'
test_board = '/tmp/board_test.kicad_pcb'
shutil.copy2(src, test_board)
board = pcbnew.LoadBoard(test_board)

# Check prerequisites
fr = FreeroutingCommands(board)
check = fr.check_freerouting({})
if not check["ready"]:
    print(f"Setup issue: {check}")
    # Fix issues before proceeding

# Run full autoroute
result = fr.autoroute({
    "boardPath": test_board,
    "maxPasses": 20,
    "timeout": 120,
})

if result["success"]:
    print(f"Routed in {result['elapsed_seconds']}s via {result['mode']}")
    print(f"Tracks: {result['board_stats']['tracks']}, Vias: {result['board_stats']['vias']}")
else:
    print(f"Failed: {result['message']} - {result.get('errorDetails', '')}")
```

### Verified Test Result (ups-12v board, 77 footprints, 44 nets)

```
=== AUTOROUTE RESULT ===
success: true
message: "Autoroute completed in 5.5s"
mode: "direct"
elapsed_seconds: 5.5
board_stats: {tracks: 639, vias: 24}
  - Tracks before: 598, after: 639 (+41 new routed tracks)
  - Vias before: 24, after: 24 (unchanged)
```
