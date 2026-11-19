# Migration inventory and gates

The private migration baseline is the preserved legacy Agent-EDA checkout at
its captured HEAD plus its tracked patch and copied untracked EDA sources.
The internal evidence inventory records hashes; it is not part of the public
distribution.

Owned capabilities to preserve and qualify:

- `eda` launcher, Agent-EDA/BoltzPCB branding, purple banner/logo/context meter.
- `.agent_eda` profile isolation, no mandatory Nous Portal sign-in, model/provider setup.
- Auto-loaded EDA design protocol, persona, prioritized electronics skills and project evidence gates.
- Datasheet prepare/parse, component validation, KiCad library find/generate, schematic generation,
  PCB placement/routing, model acquisition and independent tool environments.
- Existing session/gateway behavior, tool discovery, plugin activation, profile-scope permissions,
  and prompt caching across resumed conversations.

Qualification order: clean pinned upstream smoke; real EDA plugin registration; native component and
KiCad flows; CLI banner and exact update count; profile A/B/A isolation; gateway-backed session use;
Linux package/install/rollback; macOS and Windows package/install checks on their real hosts.
A source assembly is `qualified: false` until these checks and the full source-to-owner ledger pass.
The captured patch is a preservation record against the current canary checkout, not an automatic
patch for v0.23.1. Review each changed native path and migrate the behavior into owned code, an
adapter, or a small tested patch before promotion.

The v0.23.1 baseline and v0.23.2 candidate passed CLI help, plugin-registration,
tool-list and workflow-dispatch smoke checks in separate profiles. This does
not establish full model/provider, circuit generation, gateway, UI, platform or
release parity. The twelve per-tool environments passed their Linux import
checks. Datasheet preparation and KiCad library finding also passed native
dispatch checks. Full circuit generation, gateway, UI, model authorization,
macOS and Windows qualifications remain open.
