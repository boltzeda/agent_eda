# Controlled Hermes upgrade

Agent-EDA keeps its product source in this repository, official Hermes in the
fetch-only sibling `upstream/hermes`, generated builds in `builds`, private
profiles in `data`, and qualification records in `evidence`. The active build
is never modified in place.

## Current pins and result

- Baseline: Hermes `v0.23.1` at `3b421578588243f920886e6fe79a6faa87ea026e`.
- Candidate: Hermes `v0.23.2` at `ae2a81ccc00e9b7c06301523e5bb09608397149c`.
- The full ancestry gap is 8,154 commits, including merges. Individual commits
  need not be installed one at a time; the complete candidate must be qualified.
- Fresh source builds are `v0.23.1-eda-qualification-20261119` and
  `v0.23.2-eda-qualification-20261119`. Both have `qualified: false` in their
  assembly manifests. The launcher remains on the older prototype baseline.

The candidate passed source integrity, exact-pin reassembly, reviewed native
overlap inventory, all twelve private environment import checks, CLI/plugin
smoke checks, an installed `Timer:NE555P` / DIP-8 library lookup, and a minimal
native KiCad schematic export. The A/B/A check so
far verifies only profile configuration routing. Three focused upstream test
files passed (15 tests); a fourth test expects the literal command `hermes`
where Agent-EDA intentionally prints `eda`. This branding difference needs a
product-specific regression test and a native-test exception record. The full
native suite, realistic schematic/simulation/PCB artifacts, request-level
  profile isolation, gateway sessions, packaging/rollback, and macOS/Windows
checks remain open. These are release gates, not optional confidence notes.
The preserved old-checkout ledger records 1,516 paths. It still marks 696
patch-only paths for reconciliation and 674 copied paths for behavior review;
the `migration_parity` gate remains pending until those paths have a reviewed
owner and equivalent tested behavior or an explicit retirement decision.

Reports live at `../evidence/qualification-v0.23.1-20261119.json` and
`../evidence/qualification-v0.23.2-20261119.json`. The candidate's focused
native log is `../evidence/native-focused-v0.23.2.log`.

## Upgrade procedure

1. Keep the current build and data intact. Make a product upgrade branch.
2. Fetch official Hermes into the separate reference, review release notes,
   and lock the proposed release to its full commit SHA. Never float on `main`.
3. Compare changed native paths with the overlay/patch manifest and every
   adapter-consumed API. Record resolutions with exact diff hashes in
   `distribution/upgrade_reviews/<tag>.json`. Preserve new native behavior.
4. Assemble a new build and install dependencies from the candidate lock.
   Stage matching per-tool dependencies in a workspace-level environment
   store with `distribution/scripts/stage_tool_envs.py`. Do not edit generated
   source to fix a product problem.
5. Run `distribution/scripts/qualify.py` against both pins. It verifies source
   hashes, clean reference, overlap reviews, private tool environments,
   temporary-profile smoke, minimal KiCad artifact, profile config routing,
   and independent reassembly. Pending gates remain pending in the report.
6. Run the upstream canonical test suite and Agent-EDA integration/evaluation
   gates with temporary homes. Test real KiCad artifacts and gateway sessions,
   then package/install/rollback on Linux, macOS and Windows on those hosts.
7. Commit the product pin, adapters, tests and evidence ledger together. Rerun
   qualification on the **clean commit**. `promote.py --check` must accept it.
8. Promote only a fully passed candidate with `promote.py`. This atomically
   writes `data/active_build.json`; the launcher checks its report and assembly
   hashes. Preserve the previous build and a compatible data backup for rollback.

Example audit (the nonzero exit status is expected while gates remain open):

```bash
python3 distribution/scripts/qualify.py --product . \
  --reference ../upstream/hermes \
  --build ../builds/v0.23.2-eda-qualification-20261119 \
  --pin candidate \
  --output ../evidence/qualification-v0.23.2-20261119.json

python3 distribution/scripts/promote.py --product . \
  --report ../evidence/qualification-v0.23.2-20261119.json --check
```

The full update feed and user-facing `eda update` flow are deliberately held
until a release has passed these gates. A Git commit or source assembly alone
does not publish or install an update.
