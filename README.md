# Salience

The pre-parallel-worker P1 integration direction has resumed from `380416d`. The legacy intelligence increment is locally qualified at `81bdeee` (87 focused / 556 full non-live PASS); the broader P1 integration goal remains active. See the [integration contract](docs/v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes; production is disabled, RG0/RG1 HELD and main unmerged.

**Current checkpoint — 2026-10-05:** Resumed P1 legacy intelligence integration is locally qualified (**87 focused / 556 full non-live PASS**, 332 source hashes) at `81bdeee`. Signed compatibility commands commit canonical admission/job/outbox; fixture execution retains the original operation/permit; disposable Temporal cutover and worker hard-exit recovery are tested. See the [legacy integration contract](docs/v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 integration goal remains active; production is disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

Salience is a self-hostable, provider-neutral content operating system built around PostgreSQL, Temporal, typed agents, governed artifacts and replaceable adapters.

**Start with the [V4 production implementation blueprint](docs/v4/IMPLEMENTATION-BLUEPRINT.md).** It is the single source of truth for target architecture, verified implementation state, phases, requirement mappings, migration/rollback and release gates. V4 is partially implemented, not production-complete.

The existing Phase 1–9 foundation provides fixture-tested research, creative and governed publication workflows. Live media publishing, observation, learning, experiments and V4 admission/release controls still require the work identified in the blueprint. Approved creative changes produce a new distribution and ready-package version.

P0/DG1 provides isolated authenticated control and effects lockout. Completed P1 items 1–6 implement local admission/revisions/baselines, V2 cadence/context, V3 fixture accounting, stop/permit/cases, automatic outbox/mock trace, signed API/CLI/SDK/cutover, compatible workflow upgrades/continuation and finite waits/database notification receipts. **Item 7 independent fixture qualification is complete (2026-10-04): 132 focused / 507 full non-live PASS**, zero failures/errors/skips, 316 source hashes, three independent reviewers and all 17 P1 rows reviewed. Eight published-head findings and repair follow-ups are resolved and independently re-reviewed at `e49a214`. [Qualification evidence](docs/v4/item7-evidence.json) and [review summary](docs/v4/item7-independent-review.md) retain exact source/test/reviewer hashes. Earlier reports remain historical. Production is disabled; RG0/full RG1 remain HELD in the [ledger](docs/v4/p0-release.json).

**Current direction (2026-10-05):** The bounded parallel-agent extension is implemented and locally qualified on the feature branch. Independent research/strategy children now share one lead parent; durable signed commands support concurrent workers, scoped permissions, timeouts, ordered results and restart recovery. [Operating contract](docs/v4/parallel-agent-contract.md) and [latest evidence](docs/v4/parallel-agents-evidence.json) define the local boundary. Default execution uses fixtures, and controlled mock model transport verifies typed model outputs. Production effects remain disabled; RG0/RG1 HELD, P2–P7 gated and main `8e50d68` unmerged. The historical item-7 independent review remains tied to `e49a214`. [Repository evidence](docs/v4/repository-enforcement-evidence.json) records the inspected base; the final published head requires its own CI/protection readback. Formal independent GitHub PR approval remains NOT RUN.

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
