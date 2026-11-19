# Live checkout migration inventory

The current checkout's 1,516 changed paths are recorded individually in
an internal migration ledger. The HEAD and tracked changes are
preserved in the private migration archive; all 819
untracked files are additionally archived in
an internal archive with a SHA-256 receipt.
Neither snapshot includes the private `~/.agent_eda` profile.

The first migration pass classified 6 CLI source adapters and three guarded
source patches as smoke checked, 674 EDA skill/tool paths as copied with
behavioral review pending, 696 paths as patch-only pending reconciliation,
and 137 paths as archived only. The
ledger is a preservation and ownership queue, not a claim of full feature
parity. The archive is outside the Git repository because it includes circuit
projects, datasheets and other user-generated artifacts.

Before replacing the live installation, review each pending row, assign its
maintained owner, exercise its consumer, and record a passing test or an
explicit removal decision. Keep product-owned changes in this repository;
rebuild from the exact upstream pin instead of editing a generated build.
