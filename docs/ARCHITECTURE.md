# Agent-EDA architecture

The product repository is `agent-eda-lab/agent-eda/`. Its exact upstream pins
are in `distribution/upstream.lock.json`. `distribution/overlays/manifest.json`
declares every core file replaced for the CLI identity, records the expected
upstream digest for each pin and the owned source digest, and names its consumer
and removal condition. Assembly refuses changed upstream or product files.
`distribution/patches/manifest.json` declares guarded refusals at the Nous
Portal login and native update entrypoints. `/update` stays in the chat session.

`electronics/skills/` and `electronics/tools/` are owned EDA capabilities.
The profile installer registers the plugin adapters through Hermes's plugin
API and auto-loads the EDA design protocol. Each standalone tool has a separate
requirements file and environment. The native Hermes session, gateway and
storage code remain the authority for conversations and tool execution.

The `eda-design-workflow` skill is the single auto-loaded circuit-design
instruction. It guides order and evidence reporting. Datasheet, library,
schematic, model, and PCB plugins register independently; no workflow plugin
intercepts their calls. The PCB plugin keeps its own reviewed-placement routing
approval check. Existing profiles disable the retired `eda_workflow` plugin
when `electronics/tools/setup_eda_profile.py` is applied. A newly assembled
profile never installs that plugin.

Runtime state lives in sibling `data/`, not the product Git repository.
Builds and tool environments are disposable. The official Git reference
is fetch-only with pushes disabled. `evidence/` contains source inventories,
preservation snapshots and qualification results; it is outside product Git.
Fresh clones run `setup.py` to fetch the locked baseline, assemble a source
build, install twelve private tool environments, and create a credential-free
profile. `data/local_install.json` points the launcher at that local baseline;
its assembly digest is checked. The qualified upgrade pointer
`data/active_build.json` takes precedence and still requires a passed report.

The launcher reports the full-Git-history commit count between the selected
build and the locked upgrade candidate. `eda --check-upstream` queries the
latest stable release without changing any pin. `eda update` and `/update`
cannot install an unqualified upstream version. A release is promoted only
after source reconciliation, tool and project checks, profile migration,
rollback and platform qualification. A passing source assembly is insufficient
for promotion.

The repeatable gate auditor is `distribution/scripts/qualify.py`; its reports
remain outside source control in `evidence/`. `distribution/scripts/promote.py`
refuses an incomplete report or an artifact whose lock, source revision or
assembly hash changed. See [UPGRADE.md](UPGRADE.md) for the exact procedure and
current outstanding evidence.
