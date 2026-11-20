# Agent-EDA

Agent-EDA is BoltzPCB's electronics agent, assembled from a pinned official
Hermes release. This repository contains the Agent-EDA skills, tools, plugin
adapters, branding and setup code. Each clone builds its own Hermes runtime,
private tool environments and user profile; credentials stay on that user's
machine.

BoltzPCB-owned source is [MIT licensed](LICENSE). Bundled and fetched dependencies
retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md).

## Install from a fresh clone

Prerequisites: Git, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/).
Install KiCad, including its symbol and footprint libraries and `kicad-cli`,
for native schematic and PCB work. The PCB placement and routing tool also
needs a Python interpreter that can import KiCad's `pcbnew` binding.

### 1. Clone and install

Linux and macOS:

```sh
git clone https://github.com/boltzeda/agent_eda.git
cd agent_eda
python3 setup.py
```

Windows Command Prompt or PowerShell:

```text
git clone https://github.com/boltzeda/agent_eda.git
cd agent_eda
py -3 setup.py
```

Wait for `Agent-EDA ready` before continuing. Setup creates the runtime and
private tool environments; it does not change your shell's PATH.

### 2. Make `eda` available from any directory

Run the command for your shell **once from the cloned `agent_eda` directory**.
It saves this clone's absolute `bin` directory for future terminals. The second
line makes `eda` available immediately in the terminal you are using now.

Windows Command Prompt:

```bat
powershell -NoProfile -Command "$b=(Resolve-Path .\bin).Path; $u=[Environment]::GetEnvironmentVariable('Path','User'); $v=@($u -split ';' | Where-Object {$_}); if ($v -notcontains $b) {[Environment]::SetEnvironmentVariable('Path', (($v + @($b)) -join ';'), 'User')}"
set "PATH=%CD%\bin;%PATH%"
```

Windows PowerShell:

```powershell
$b=(Resolve-Path .\bin).Path; $u=[Environment]::GetEnvironmentVariable('Path','User'); $v=@($u -split ';' | Where-Object {$_}); if ($v -notcontains $b) {[Environment]::SetEnvironmentVariable('Path', (($v + @($b)) -join ';'), 'User')}
$env:Path="$b;$env:Path"
```

Linux Bash:

```bash
printf '\nexport PATH="%s/bin:$PATH"\n' "$PWD" >> ~/.bashrc
export PATH="$PWD/bin:$PATH"
```

macOS Zsh:

```zsh
printf '\nexport PATH="%s/bin:$PATH"\n' "$PWD" >> ~/.zshrc
export PATH="$PWD/bin:$PATH"
```

### 3. Choose a provider and start

```text
eda --status
eda model
eda
```

You can run these commands from any directory. The PATH entry is permanent;
you do not need to run setup or `activate` again in a new terminal. To check
that it works in future terminals, close the current terminal and open a new
one. On Windows, **fully close and reopen VS Code** before using a new
integrated terminal; opening another tab in an already running VS Code may
keep its old PATH. Use `where eda` on Windows or `command -v eda` on Linux and
macOS to see which clone supplies the command.

Windows Git may use CRLF checkout line endings. Setup verifies the reviewed
CLI adapters after normalizing those line endings, and the repository requests
LF for future checkouts.

`eda model` lets each user choose and authenticate their own provider. Setup
does not copy credentials and does not use Nous Portal. On first run, setup
fetches the exact pinned Hermes commit into a separate, fetch-only reference,
assembles a build, installs the runtime from its lockfile, creates twelve
separate tool environments from the tracked requirements, and creates a
private Agent-EDA profile. The downloads and private environments can take
time and disk space. Rerunning `python3 setup.py` resumes an interrupted
installation without overwriting a configured profile.

If KiCad's `pcbnew` binding is attached to a particular Python, pass
`--pcb-python <path>` to setup. The runtime launcher accepts `AGENT_EDA_HOME`
and `AGENT_EDA_BUILD` overrides for maintainers. Linux is the verified setup
host; native macOS and Windows installation checks remain open.

On Windows, the PCB tool's setup check searches standard KiCad installations
for `pcbnew.py` and its native libraries. For a custom installation, set
`KICAD_ROOT` to its root directory before running setup. If KiCad's binding
uses a different Python version, rerun setup with `--pcb-python` pointing to
a compatible interpreter; setup recreates only that tool's environment when
the interpreter changes. KiCad itself and its Python binding must be installed
separately.

Tools and requirements are versioned here. Generated `.venv` directories,
Hermes builds, profiles, and authentication files are deliberately outside the
Git repository. See [architecture](docs/ARCHITECTURE.md) for that boundary.

## Updates

`/update` in the chat and `eda update` in a terminal fetch new commits from
the Agent-EDA GitHub `main` branch and install them in this clone. The updater
requires a clean, fast-forward source checkout. It assembles a new build,
installs the locked runtime, reuses unchanged private tool environments,
preserves the user's provider settings and session history in a new profile,
checks the twelve tools and CLI startup, then switches the local installation.
The previous build and profile remain available if verification fails.

The first public snapshot shipped with `/update` disabled. Users who installed
that snapshot need this one-time bootstrap in their clone:

```bash
git pull --ff-only origin main
python3 distribution/scripts/update_product.py
```

After that, `/update` and `eda update` install future Agent-EDA commits. They
do not advance the separately pinned Hermes release. `eda --status` reports
the Hermes commit gap, and `eda --check-upstream` checks its latest stable tag.
Hermes pin changes still follow [UPGRADE.md](docs/UPGRADE.md).

## Maintainers

The workspace layout is the product clone beside `upstream/hermes/`, `builds/`,
`data/`, `environments/`, and `evidence/`. The product Git repository contains
only maintained source. `distribution/scripts/assemble.py` enforces the
upstream tag and commit, overlay hashes, and fetch-only upstream push setting.
`distribution/scripts/qualify.py` and `promote.py` preserve the controlled
upgrade and rollback path. See [migration status](docs/MIGRATION.md) for
outstanding release gates.
