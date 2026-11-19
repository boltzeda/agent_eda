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

`eda --status` reports the commit gap between the installed pinned Hermes
source and the next candidate. `eda --check-upstream` checks the latest stable
tag without changing the installation. `eda update` and `/update` do not
replace a pinned build until the candidate passes the qualification process
in [UPGRADE.md](docs/UPGRADE.md). Setup is for a fresh baseline install; it is
not an upgrade command.

## Maintainers

The workspace layout is the product clone beside `upstream/hermes/`, `builds/`,
`data/`, `environments/`, and `evidence/`. The product Git repository contains
only maintained source. `distribution/scripts/assemble.py` enforces the
upstream tag and commit, overlay hashes, and fetch-only upstream push setting.
`distribution/scripts/qualify.py` and `promote.py` preserve the controlled
upgrade and rollback path. See [migration status](docs/MIGRATION.md) for
outstanding release gates.
