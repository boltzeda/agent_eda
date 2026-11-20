![Agent-EDA purple wordmark](assets/agent-eda-wordmark.png)

# Agent-EDA

An electronics design agent by **BoltzPCB**.

Describe a circuit in the terminal, then use Agent-EDA to work through component research, KiCad libraries, schematic generation, simulation, and PCB layout. The agent uses an auto-loaded design workflow and reports which parts of a design it verified, skipped, or could not complete.

[Installation](#installation) · [First steps](#first-steps) · [Capabilities](#capabilities) · [Commands](#commands) · [Updates](#updates) · [Troubleshooting](#troubleshooting)

## Capabilities

- **Circuit design workflow:** clarify requirements, identify blocks and pins, verify symbols and footprints, generate a schematic, simulate where models and tools permit, then review placement before routing.
- **KiCad work:** find or generate component libraries, generate native schematics, and run the PCB placement and routing workflow.
- **Component research:** prepare and parse datasheets, validate structured component data, and acquire supported vendor models.
- **Independent tools:** datasheet, library, schematic, model, and PCB tools can each be called directly. The workflow skill guides their order without blocking tool calls.
- **Your model provider:** choose a provider with `eda model`. Setup does not copy credentials or require Nous Portal login.
- **Persistent local work:** conversations and configuration live in a private profile outside the Git checkout.

Generated schematics and boards still need engineering review, KiCad checks, and verification against the selected components' datasheets.

## Installation

Install as your normal user. You need **Python 3.11 or newer and [uv](https://docs.astral.sh/uv/)**. Install **KiCad** with its symbol and footprint libraries and `kicad-cli` for native EDA work. PCB placement and routing also need a Python interpreter that can import KiCad's `pcbnew` binding.

First-time setup may take several minutes. Wait for `Agent-EDA ready` before continuing. If setup stops, fix the reported issue and run the same setup command again.

### Linux

```bash
git clone https://github.com/boltzeda/agent_eda.git
cd agent_eda
python3 setup.py
```

Register this clone's launcher once, then use it immediately:

```bash
printf '\nexport PATH="%s/bin:$PATH"\n' "$PWD" >> ~/.bashrc
export PATH="$PWD/bin:$PATH"
eda model
eda
```

Open a new Bash terminal later and run `eda` from any directory. For another shell, add this checkout's absolute `bin` directory to that shell's startup configuration.

### macOS

```zsh
git clone https://github.com/boltzeda/agent_eda.git
cd agent_eda
python3 setup.py
printf '\nexport PATH="%s/bin:$PATH"\n' "$PWD" >> ~/.zshrc
export PATH="$PWD/bin:$PATH"
eda model
eda
```

These commands assume the default Zsh shell. Native macOS installation checks are still open; see [platform notes](#platform-notes).

### Windows Command Prompt

Install Git for Windows, Python, uv, and KiCad first. In Command Prompt:

```bat
git clone https://github.com/boltzeda/agent_eda.git
cd agent_eda
py -3 setup.py
```

Save this clone's `bin` directory to your **User PATH** once, and refresh this Command Prompt for immediate use:

```bat
powershell -NoProfile -Command "$b=(Resolve-Path .\bin).Path; $u=[Environment]::GetEnvironmentVariable('Path','User'); $v=@($u -split ';' | Where-Object {$_}); if ($v -notcontains $b) {[Environment]::SetEnvironmentVariable('Path', (($v + @($b)) -join ';'), 'User')}"
set "PATH=%CD%\bin;%PATH%"
eda model
eda
```

The PowerShell command saves PATH for future processes. The `set` command refreshes **only this Command Prompt**. If you omit `set`, close this terminal and open a new one before running `eda`. If VS Code was already open, fully exit and reopen VS Code; a new tab in the old process can inherit its previous PATH.

### Windows PowerShell

```powershell
git clone https://github.com/boltzeda/agent_eda.git
cd agent_eda
py -3 setup.py
$b=(Resolve-Path .\bin).Path
$u=[Environment]::GetEnvironmentVariable('Path','User')
$v=@($u -split ';' | Where-Object {$_})
if ($v -notcontains $b) { [Environment]::SetEnvironmentVariable('Path', (($v + @($b)) -join ';'), 'User') }
$env:Path="$b;$env:Path"
eda model
eda
```

The saved User PATH works in future terminals. `$env:Path` refreshes the current PowerShell session. To check which installation you are launching, run `where.exe eda` in PowerShell or `where eda` in Command Prompt.

### Platform notes

Setup does not install KiCad or its Python binding. On Windows it searches standard KiCad installations for `pcbnew.py` and native libraries. For a custom installation, set `KICAD_ROOT` before setup. If the binding uses a different Python version, rerun setup with `--pcb-python <path-to-compatible-python>`; setup recreates only the PCB tool environment when that interpreter changes.

Linux is the verified setup host. Native macOS and Windows installation qualification remains open. On Windows, Git may check out files with CRLF endings; setup normalizes line endings when verifying the reviewed CLI adapters.

The checkout must remain where you installed it because its launcher points to the sibling build and data directories. Generated builds, virtual environments, profiles, and credentials are outside the Git repository. See [architecture](docs/ARCHITECTURE.md).

## First steps

1. Run `eda model` in your operating-system terminal and choose a provider using your own account or API key.
2. Run `eda` to open chat. Type `/help` for interactive commands.
3. Try: “Design a 12 V to 5 V buck converter with an LM2596 and produce a KiCad schematic.”
4. Review the agent's component choices, electrical assumptions, verification results, and saved file paths before using a design.

For a PCB request, Agent-EDA's workflow asks for requirements and checks the component libraries before schematic generation. It then uses simulation when a usable model and simulator are available. The PCB workflow includes placement review before routing. You can explicitly ask to skip a step; skipped work should be reported as unverified.

## Commands

Run shell commands in your operating-system terminal. Run slash commands **inside** an `eda` chat.

| Command | Purpose |
| --- | --- |
| `eda` | Open Agent-EDA |
| `eda model` | Choose or change your model provider |
| `eda --status` | Show the installed build and Hermes commit gap |
| `eda --check-upstream` | Check the latest stable Hermes release without changing the pin |
| `eda update` | Install qualified Agent-EDA source updates from this repository |
| `/help` | Show interactive chat commands |
| `/model` | Choose a model in the current chat |
| `/skills` | Inspect available skills |
| `/update` | Exit chat and start the Agent-EDA updater |

For example, type `eda model` after the Windows `C:\...>` prompt. Type `/model` only after Agent-EDA has opened.

## Updates

For an installed Git checkout, run `eda update` in a terminal or `/update` inside chat. The updater fetches the Agent-EDA repository's `main` branch, requires a clean checkout and a fast-forward update, assembles and checks the new build, then switches the local installation. It preserves provider settings and session history in the new profile. A failed check leaves the previous build available.

The first public snapshot shipped without a working `/update`. If you installed that snapshot, bootstrap it once from the checkout:

```bash
git pull --ff-only origin main
python3 distribution/scripts/update_product.py
```

On Windows, use `py -3 distribution/scripts/update_product.py` for the second
command.

After that, use `eda update` or `/update` for later Agent-EDA commits. These commands do not automatically advance the separately pinned Hermes release. See [Hermes upgrade procedure](docs/UPGRADE.md).

## Troubleshooting

| Problem | Check |
| --- | --- |
| `eda` is not recognized | Run the PATH command for your shell above. Refresh the current terminal or open a new one; fully restart VS Code if it was already open. |
| Setup was interrupted | Fix the reported error and rerun `python3 setup.py` or `py -3 setup.py` from this checkout. |
| `pcbnew` cannot import | Install KiCad's Python binding, then use `KICAD_ROOT` or `--pcb-python` as described in Platform notes. |
| No model response | Run `eda model` in the system terminal and complete provider authentication. |
| Arrow keys do not move an old Windows model menu | Type the provider's displayed number and press Enter, then update Agent-EDA for the scrolling picker. |
| A generated file is missing | Check the tool error and reported output path. A skill being loaded does not mean generation succeeded. |

## Documentation

- [Architecture and state boundaries](docs/ARCHITECTURE.md)
- [Hermes upgrade procedure](docs/UPGRADE.md)
- [Migration status](docs/MIGRATION.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

BoltzPCB-owned code is [MIT licensed](LICENSE). Bundled and fetched third-party code retains its applicable license and notices.
