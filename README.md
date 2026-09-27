# Salience

Salience is a self-hostable, provider-neutral content operating system built around PostgreSQL, Temporal, typed agents, governed artifacts and replaceable adapters.

**Start with the [V4 production implementation blueprint](docs/v4/IMPLEMENTATION-BLUEPRINT.md).** It is the single source of truth for target architecture, verified implementation state, phases, requirement mappings, migration/rollback and release gates. V4 is partially implemented, not production-complete.

The existing Phase 1–9 foundation provides fixture-tested research, creative and governed publication workflows. Live media publishing, observation, learning, experiments and V4 admission/release controls still require the work identified in the blueprint. Approved creative changes produce a new distribution and ready-package version.

P0 provides isolated authenticated control, effects lockout, scoped credentials, immutable verified storage/retention and correlated audit/tracing. DG1 passes; local P1 adds canonical admission, distinct deferral/recovery identities, outbox/Temporal process recovery, atomic revisions and explicit fixture-baseline approval. V2 adds scoped goal/context/authority contracts and bounded cadence/coalescing; V3 adds fixture-only G0 allocations and G1 transfers over the canonical cost ledger. Item 3 adds serialized stop/permit claims and bound recovery cases; item 4 adds automatic bounded fixture dispatch, durable dead-letter escalation and a permit-gated no-effects mock adapter trace. Item 5a adds signed opt-in local fixture API/CLI/SDK commands with exact goal-creation receipts. Latest local qualification (2026-09-27): **408 non-live and 83 focused tests PASS**, no failures/errors/skips. See [validation](docs/v4/validation.md) and [item-5a evidence](docs/v4/item5a-evidence.json). Production remains disabled; RG0/full RG1 remain **HELD** in the [release ledger](docs/v4/p0-release.json).

**Current direction (2026-09-27):** P1 items 1–4 are locally verified; item 5 is public API/CLI/SDK command parity and canonical schedule/legacy start-signal cutover. Worker upgrades and durable review timers remain planned. The item-4 API parent comes from an authenticated P0 request, not a public V4 command; the adapter is an injected no-effects mock, not a production provider. P2–P7 have not started. Follow [current progress](docs/v4/progress.md#current-state--2026-09-24) and the [ordered queue](docs/v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). Required checks and strict protection pass on item-4 implementation `2fbb384`; any later head needs its own checks. The feature branch is not merged into `main`; independent approval remains required before any separately authorized merge.

```bash
python3 docs/v4/check.py --self-test
```

This documentation gate verifies source intent, 70 original requirements plus five foundational obligations, acyclic phase/release dependencies, evidence drift, preserved archives and links. CI runs documentation and non-live application regressions. Main requires both checks, strict up-to-date status and independent PR approval with administrator enforcement; local test success alone cannot authorize a merge.

Existing fixture verification commands remain available:

```bash
bash scripts/verify-phase-9.sh
bash scripts/verify-phases-7-8.sh
bash scripts/verify-browser-evidence.sh
```

The pinned Playwright headless runtime is provisioned locally for tests; a fresh checkout still needs browser installation. Default Compose is for private fixtures; `compose.p0.yaml` is an effects-disabled, isolated qualification profile, not production enablement. Use the blueprint's release gates before exposing ingress or real accounts.

- [Inventory and preservation record](docs/v4/inventory.json)
- [Current execution progress and historical checkpoints](docs/v4/progress.md)
- [Historical archive and restoration instructions](docs/archive/2026-09-22/README.md)
- [Original V4 design sources](docs-new-arch/README.md)
- [Historical Phase 1–9 interactive architecture](docs/salience-phase-1-9.architecture.html)

Historical diagrams describe prior implementation boundaries; the blueprint contains the complete V4 target overview and focused workflows.

<!-- Historical heading anchors retained for incoming links. -->
<a id="salience"></a>
<a id="architecture-at-a-glance"></a>
<a id="what-you-can-rely-on-today"></a>
<a id="a-canonical-operational-core"></a>
<a id="governance-before-effects"></a>
<a id="an-evidence-linked-intelligence-loop"></a>
<a id="governed-creative-production-and-distribution"></a>
<a id="provider-neutral-extensions"></a>
<a id="run-a-safe-demo"></a>
<a id="prerequisites"></a>
<a id="public-control-surface"></a>
<a id="verify-the-intelligence-loop"></a>
<a id="verify-browser-evidence"></a>
<a id="what-is-deliberately-not-here"></a>
<a id="repository-guide"></a>
<a id="design-principle"></a>
