# Salience

Salience is a self-hostable, provider-neutral content operating system built around PostgreSQL, Temporal, typed agents, governed artifacts and replaceable adapters.

**Start with the [V4 production implementation blueprint](docs/v4/IMPLEMENTATION-BLUEPRINT.md).** It is the single source of truth for target architecture, verified starting state, implementation phases, requirement mappings, migration/rollback and release gates. V4 is a plan, not a production-completion claim.

The existing Phase 1–9 foundation provides fixture-tested research, creative and governed publication workflows. Live media publishing, observation, learning, experiments and V4 admission/release controls still require the work identified in the blueprint. Approved creative changes produce a new distribution and ready-package version.

P0 provides an isolated authenticated control boundary, effects lockout, scoped credential leases, immutable verified storage and retention hooks, and correlated audit/tracing. DG1 local-development entry passes; the first P1 increment adds dry-run-only canonical admission and distinct deferral/recovery/closure semantics. Verification: **284 non-live tests pass, with no skips**, including 39 focused P0 checks and 13 P1 admission cases. See [verification details](docs/v4/validation.md). Production remains disabled; [the release ledger](docs/v4/p0-release.json) keeps RG0 and full P1/RG1 **HELD** with remaining obligations explicit.

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
- [Documentation transition checkpoint](docs/v4/progress.md)
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
