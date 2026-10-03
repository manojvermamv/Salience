# Salience

Salience is a self-hostable, provider-neutral content operating system built around PostgreSQL, Temporal, typed agents, governed artifacts and replaceable adapters.

**Start with the [V4 production implementation blueprint](docs/v4/IMPLEMENTATION-BLUEPRINT.md).** It is the single source of truth for target architecture, verified implementation state, phases, requirement mappings, migration/rollback and release gates. V4 is partially implemented, not production-complete.

The existing Phase 1–9 foundation provides fixture-tested research, creative and governed publication workflows. Live media publishing, observation, learning, experiments and V4 admission/release controls still require the work identified in the blueprint. Approved creative changes produce a new distribution and ready-package version.

P0 provides isolated authenticated control, effects lockout, scoped credentials, immutable verified storage/retention and correlated audit/tracing. DG1 passes; local P1 adds canonical admission, distinct deferral/recovery identities, outbox/Temporal process recovery, atomic revisions and explicit fixture-baseline approval. V2 adds scoped goal/context/authority contracts and bounded cadence/coalescing; V3 adds fixture-only G0 allocations and G1 transfers over the canonical cost ledger. Item 3 adds serialized stop/permit claims and bound recovery cases; item 4 adds automatic bounded fixture dispatch, durable dead-letter escalation and a permit-gated no-effects mock adapter trace. Items 5a–5c add signed opt-in local fixture API/CLI/SDK commands, exact goal/governance receipts and one-goal schedule cutover with an independent bounded fixture poller. Item 6 adds compatible worker-history replay, bounded Continue-As-New, finite canonical waits/review deadlines, owner holds and no-effects database notification receipts. Item-6 qualification remains **85 focused / 456 full PASS** in its immutable reports. Latest item-7 preparation/owner-binding qualification (2026-10-03): **32 focused / 462 full non-live PASS**, zero failures/errors/skips, 311 source files; [preparation evidence](docs/v4/item7-preparation-evidence.json). Independent review remains NOT RUN. See [validation](docs/v4/validation.md) and [initial item-6 evidence](docs/v4/item6-evidence.json) and [final operator correction](docs/v4/item6-operator-evidence.json). Production remains disabled; RG0/full RG1 remain **HELD** in the [release ledger](docs/v4/p0-release.json).

**Current direction (2026-10-03):** P1 items 1–6 are implemented in local no-effects fixtures. Item 7 is in progress: qualification preparation and local owner/migration-race fixes pass; independent full RG1 review remains NOT RUN. Continue with that independent review and the remaining live schedule, deployment routing, production provider/alert and scoped remote reconciliation obligations. Notifications use a local PostgreSQL fixture sink; no external alert is sent. P2–P7 have not started. Follow [current progress](docs/v4/progress.md#current-state) and the [ordered queue](docs/v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). Published preparation base `91784e75b3a0deeaac6c957150908845abe27f7d` passes both required checks and strict main protection at 2026-10-03T17:12:40.580583+00:00; [repository evidence](docs/v4/repository-enforcement-evidence.json) binds that exact base. The newly qualified owner-binding candidate requires its own published-head inspection. Every later published commit requires its own inspection. The feature branch remains unmerged; current independent PR approval and separate merge authorization are required.

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
