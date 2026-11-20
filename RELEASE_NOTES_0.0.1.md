# AGENT EDA 0.0.1 Release Notes

AGENT EDA 0.0.1 is the first public source release of BoltzPCB's electronics design agent. It brings circuit-design guidance and EDA tools into a terminal assistant built on a pinned Hermes foundation. The schematic-to-PCB workflow has been run end to end, with the agent able to work through circuit definition, KiCad schematic generation, and PCB creation in one design task.

## 1. Circuit design from requirements to PCB

AGENT EDA starts by establishing the circuit's purpose, operating conditions, functional blocks, and component pin connections. Before generating a schematic, it checks whether the required KiCad symbols and footprints are available and can use its library tools to find or create missing assets.

The workflow then proceeds to a native KiCad schematic. When a compatible circuit model and simulator are available, it can run relevant simulation and report what the result does and does not verify. For PCB work, the agent moves from the schematic to placement review and board layout; routing follows the PCB workflow's placement review step. Users may ask to skip a stage, and the agent should identify any skipped or unverified work in its report.

The tools for datasheets, component validation, KiCad libraries, schematics, models, and PCB work are independently callable. The auto-loaded design workflow guides their use without requiring a separate workflow-tracking tool before they can run.

## 2. Model choice and private work

Users choose their own model provider with `eda model`. AGENT EDA does not require a Nous Portal login or include another user's credentials. Its local profile keeps configuration, conversations, and provider authentication outside the source repository.

The `eda` command opens the interactive CLI. The terminal identity, banner, and circuit logo use AGENT EDA's purple branding, while the pinned Hermes source supplies the underlying agent runtime.

## 3. Install and source downloads

For installation steps on Linux, macOS, and Windows, follow the [README](README.md). Setup requires KiCad with a compatible `pcbnew` Python binding, plus the prerequisites listed there. The first setup may take several minutes while it prepares the runtime and tool environments.

GitHub provides **Source code (zip)** and **Source code (tar.gz)** archives for the release tag. These are source snapshots, not prebuilt installers. Clone the repository if you want the built-in `eda update` or `/update` command: those commands currently require a Git checkout of the Agent-EDA repository.

## 4. Updates

In an installed Git checkout, run `eda update` in a terminal or `/update` inside AGENT EDA to install new changes from the Agent-EDA repository. Updates retain provider settings and chat history. The Hermes version remains pinned separately; product updates do not silently replace it with a newer Hermes release.

## 5. Platform status and known issue

- Linux is the verified setup host. Native installation qualification for macOS and Windows is still in progress.
- The Windows `eda model` picker has a known selection problem: choosing a provider or model may not complete. This issue remains open in 0.0.1.
- KiCad and its compatible `pcbnew` Python binding must be installed separately to complete the current setup. Generated electrical and PCB outputs still require review against the chosen components and design requirements.

AGENT EDA is built by **BoltzPCB**. BoltzPCB-owned code is [MIT licensed](LICENSE); bundled and fetched third-party code retains its own terms and [notices](THIRD_PARTY_NOTICES.md).
