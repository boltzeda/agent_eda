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

```bash
git clone <Agent-EDA repository URL>
cd agent-eda
python3 setup.py
source ./activate
eda model
eda
```

Sourcing this checkout's `activate` selects its launcher even if an older
Agent-EDA checkout previously defined an `eda` shell function. A plain `eda`
command in a new shell uses whichever executable is first on `PATH`; check
`command -v eda` if multiple checkouts are installed.

`eda model` lets each user choose and authenticate their own provider. Setup
does not copy credentials and does not use Nous Portal. On first run, setup
fetches the exact pinned Hermes commit into a separate, fetch-only reference,
assembles a build, installs the runtime from its lockfile, creates twelve
separate tool environments from the tracked requirements, and creates a
private Agent-EDA profile. The downloads and private environments can take
time and disk space. Rerunning `python3 setup.py` resumes an interrupted
installation without overwriting a configured profile.

For macOS, use the same commands in a shell with `python3` and `uv`. For
Windows PowerShell, run `py -3 setup.py`, then `. .\activate.ps1` and
`eda model`; in Command Prompt use `py -3 setup.py`, `call activate.cmd`,
and `eda model`. If KiCad's `pcbnew` binding is attached to a particular Python, pass
`--pcb-python <path>` to setup. The runtime launcher accepts `AGENT_EDA_HOME`
and `AGENT_EDA_BUILD` overrides for maintainers. Linux is the verified setup
host; native macOS and Windows installation checks remain open.

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
