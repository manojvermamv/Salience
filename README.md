# Salience V4

Salience is a self-hostable, provider-neutral content operating system built around PostgreSQL, Temporal workflows, typed agents, governed artifacts, and replaceable adapters. The V4 blueprint describes the full target architecture. The current application is locally qualified within no-effects fixture boundaries; production is not fully integrated or released.

## Current status and evidence

Three evidence anchors describe different things:

- **Application qualification:** `a4b2368cdb463939d093020224a5b5667392aee6` passed **21 focused / 604 full non-live tests**, with **352 source hashes**. This locally qualifies the original legacy intelligence, selected-brief, no-send dummy, dry-creative, governed-publication stages, and both original native schedule conversions with resumed ingress. Qualification uses isolated fixtures, a zero publication budget, and disabled effects. See the [application evidence](docs/v4/legacy-publication-evidence.json).
- **Published feature/documentation checkpoint:** `a2e73c7242d67388e8cd3be4361283cd8e6c081a`.
- **CI readback for that exact checkpoint:** [run 37346137016](https://github.com/manojvermamv/Salience/actions/runs/37346137016) passed both `verify` and `p0-regression`; `p0-regression` reported **604 passed** and **three warnings**. This readback is bound to the checkpoint above. A later documentation commit needs its own exact-head readback after publication.

P0 and P1 items 1–7 are locally qualified inside no-effects fixture boundaries. The accepted P1/P3 corrections are included in the fresh application qualification; exact corrected-head publication rereview remains pending. A CI pass or local fixture result does not complete that rereview or authorize production.

| Area | Current evidence | Production status |
| --- | --- | --- |
| P0 foundation and P1 items 1–7 | Locally qualified within no-effects fixture boundaries; the current application anchor is `a4b2368cdb463939d093020224a5b5667392aee6` | Effects disabled; RG0 and RG1 are **HELD** |
| Original legacy stages and native schedules | Original intelligence, selected brief, no-send dummy, dry creative, governed publication, and both original native schedule conversions locally qualified | Fixture-only; no live provider or production schedule routing qualification |
| Later phases | P2–P7 gated/planned; optional P6 disabled | Not released |
| Main branch | `8e50d68efbf2bec2aabfb2fe3226e01635de2adb` | Unmerged; formal independent PR approval remains NOT RUN |

Local green status below means implemented and qualified in a fixture scope. It does not mean production-ready. Production effects remain disabled, RG0/RG1 remain held, and production is not fully integrated or released.

## V4 target architecture

This overview shows the complete target and the status of each area. The arrows into held or planned areas show intended architecture; they do not assert that external effects or later phases are enabled.

```mermaid
flowchart TB
  subgraph ingress["People and ingress"]
    direction LR
    PEOPLE["Operators and program owners"]
    SIGNED["Signed API, SDK, and CLI"]
    SCHEDULES["Native schedules"]
    PEOPLE --> SIGNED
  end

  subgraph localPath["LOCAL: fixture-qualified implementation only; not production-ready"]
    GOVERN["P0/P1 governance and admission<br/>Production gates held"]
    STATE["PostgreSQL canonical state<br/>Transactional outbox and inbox"]
    DISPATCH["Ordered dispatcher<br/>Token-fenced delivery"]
    TEMPORAL["Temporal workflows<br/>Compatible workers"]
    RESEARCH["Research and evidence"]
    BRIEFS["Selected briefs"]
    CREATIVE["Creative production"]
    PUBLICATION["Governed publication<br/>Zero-budget fixture"]
  end
  subgraph parallelLane["SEPARATE local callable-agent fixture lane"]
    PARALLEL_COMMAND["Separate signed fixture command"]
    AGENTS["Bounded parallel AI agents<br/>Separately qualified extension"]
    PARALLEL_COMMAND --> AGENTS
  end
  EFFECTS["External providers and effects<br/>PRODUCTION HELD; effects disabled"]
  OBSERVATION["Observation and outcomes<br/>PLANNED / GATED"]
  LEARNING["Learning proposals<br/>PLANNED / GATED"]
  RELEASE["Strategy and capability release<br/>PLANNED / GATED"]
  TRAINING["Optional training<br/>OPTIONAL; P6 disabled"]
  OPS["Operations, restore, and rollout<br/>PRODUCTION HELD"]

  SIGNED --> GOVERN
  SIGNED -.-> PARALLEL_COMMAND
  SCHEDULES --> GOVERN
  GOVERN --> STATE
  STATE --> DISPATCH
  DISPATCH --> TEMPORAL
  TEMPORAL --> RESEARCH
  RESEARCH --> BRIEFS
  BRIEFS --> CREATIVE
  CREATIVE --> PUBLICATION
  PUBLICATION -.-> EFFECTS
  EFFECTS -.-> OBSERVATION
  OBSERVATION -.-> LEARNING
  LEARNING -.-> RELEASE
  RELEASE -.-> TRAINING
  STATE -.-> OPS
  TEMPORAL -.-> OPS
  EFFECTS -.-> OPS

  classDef local fill:#e4f3e7,stroke:#347a46,color:#173b20
  classDef held fill:#ffe8d8,stroke:#b45b18,color:#572b0c
  classDef planned fill:#e6efff,stroke:#3b67a0,color:#193354
  classDef optional fill:#eee8f5,stroke:#71518f,color:#362247
  class GOVERN,STATE,DISPATCH,TEMPORAL,RESEARCH,BRIEFS,CREATIVE,PUBLICATION,AGENTS,PARALLEL_COMMAND local
  class EFFECTS,OPS held
  class OBSERVATION,LEARNING,RELEASE planned
  class TRAINING optional
```

P0/P1 controls govern signed commands and schedule ticks before work is admitted. PostgreSQL commits canonical identities, context, allocation, admission, and outbox records together. The target overview groups product phases; it does not mean one legacy execution automatically traverses every stage. The bounded parallel-agent extension has its own signed fixture command and worker lane, separate from the original legacy intelligence workflow. In the target system, provider outcomes feed observation, learning proposals, and controlled strategy or capability release. Those later phases, real provider effects, and production operations remain gated.

## Locally qualified governed execution path

The following path describes the local implementation boundary. Each admitted operation selects exactly one legacy stage from its immutable binding. Stage activities recheck current authority; the internal fixture adapters cannot send externally.

```mermaid
flowchart TB
  subgraph ingress["Signed and scheduled ingress"]
    direction LR
    CMD["Signed command or native schedule tick<br/>LOCAL fixture-qualified"]
    AUTH["Authenticate subject; check current scoped grant,<br/>stop revision, baseline, and policy"]
    ADMIT["Admit under current scope and limits"]
    ID["Bind goal, intent, cycle, context,<br/>and operation identity"]
    CMD --> AUTH --> ADMIT --> ID
  end

  subgraph durable["Atomic state and ordered delivery"]
    direction LR
    COMMIT["Atomic PostgreSQL commit:<br/>allocation, admission, context, and outbox"]
    DB["Canonical PostgreSQL state<br/>Transactional outbox and inbox"]
    DISPATCH["Ordered token-fenced dispatcher"]
    COMMIT --> DB --> DISPATCH
  end

  subgraph runtime["Canonical Temporal control workflow"]
    CTRL["Compatible control workflow<br/>Replay, Continue-As-New, finite waits,<br/>and review deadlines"]
    CONSUME["Consume activity"]
    RECEIPT["Durable inbox receipt committed"]
    PERMIT["Issue and claim fixture permit"]
    STAGECHECK["Recheck current permit, policy, and bound stage;<br/>account, approval, and rights as applicable"]
    SELECT["Dispatch exactly ONE stage selected<br/>by the immutable admitted binding"]
    CTRL --> CONSUME --> RECEIPT --> PERMIT --> STAGECHECK --> SELECT
  end

  subgraph stages["Alternative bound legacy workflows: one per admission"]
    direction TB
    INTEL["Original intelligence workflow"]
    BRIEF["Selected-opportunity intelligence variant/output:<br/>selected brief"]
    DUMMY["No-send dummy workflow"]
    CREATIVE["Dry creative workflow"]
    NO_PACKAGE["No media asset or ready package"]
    PUB["Governed publication workflow:<br/>exact package, account, approval, and budget"]
    INTEL --> BRIEF
    CREATIVE --> NO_PACKAGE
  end
  READY["Separately supplied exact ready package;<br/>not produced by dry creative"]

  subgraph parallelLane["Separate local callable-agent lane"]
    TEAM_COMMAND["Separate signed fixture API, SDK, or CLI command"]
    TEAM_WORKER["Dedicated parallel-agent worker boundary"]
    FANOUT["Bounded parallel agents<br/>separately fixture-qualified"]
    TEAM_COMMAND --> TEAM_WORKER --> FANOUT
  end

  FIXTURE["Internal fixture/mock boundary<br/>zero budget; effects disabled"]
  EXTERNAL["External provider boundary<br/>PRODUCTION HELD"]
  RECORDS["Immutable receipts; liability and reservation;<br/>cases and notifications; reconciliation and archive"]
  TRACE["Trace, audit, and provenance link identities<br/>across ingress, state, workflow, and stage results"]
  OPS["Rollback keeps patched histories on compatible workers<br/>LOCAL fixture restart only; production routing and restore HELD"]

  ID --> COMMIT
  DISPATCH --> CTRL
  SELECT --> INTEL
  SELECT --> DUMMY
  SELECT --> CREATIVE
  SELECT --> PUB
  READY --> PUB
  PUB --> FIXTURE
  FIXTURE -.-> EXTERNAL
  EXTERNAL -.-> RECORDS
  BRIEF --> RECORDS
  DUMMY --> RECORDS
  NO_PACKAGE --> RECORDS
  PUB --> RECORDS
  RECORDS --> DB
  PUB -.-> TRACE
  CTRL -.-> TRACE
  CTRL -.-> OPS

  classDef local fill:#e4f3e7,stroke:#347a46,color:#173b20
  classDef held fill:#ffe8d8,stroke:#b45b18,color:#572b0c
  classDef planned fill:#e6efff,stroke:#3b67a0,color:#193354
  classDef optional fill:#eee8f5,stroke:#71518f,color:#362247
  class CMD,AUTH,ADMIT,ID,COMMIT,DB,DISPATCH,CTRL,CONSUME,RECEIPT,PERMIT,STAGECHECK,SELECT,INTEL,BRIEF,DUMMY,CREATIVE,NO_PACKAGE,READY,PUB,TEAM_COMMAND,TEAM_WORKER,FANOUT,FIXTURE,RECORDS,TRACE local
  class EXTERNAL,OPS held
```

The shared delivery order is dispatcher, compatible canonical control workflow, consume activity, then durable inbox receipt. After the receipt is recorded, the control activity issues and claims the fixture permit; current policy and the immutable stage binding are checked before the adapter dispatches the one selected legacy workflow. Replay, Continue-As-New, finite waits, and review deadlines belong to this canonical control workflow.

The stage branches are alternatives with separate admissions, not a serial chain. The intelligence workflow supports the selected-opportunity variant that produces a selected brief. The dry-creative fixture produces no ready package. Governed publication consumes a separately supplied exact ready package and its own account, approval, and budget bindings. The bounded parallel-agent extension is a separate signed fixture command and worker lane; no edge claims the original legacy intelligence workflow invokes it. Stage receipts, liabilities, reservations, cases, notifications, reconciliation, archive, trace, audit, and provenance remain linked to canonical state.

Patched workflow histories require compatible workers through rollback. Local fixture rollback and restart evidence does not qualify production worker routing, coordinated restore, or external-effect recovery. The held provider boundary remains closed until the required production gates pass.

## What is and is not production-ready

The local results qualify specific no-effects fixtures. They do not qualify live providers, real production accounts, nonzero publication budgets, production schedule inventory or routing, production-wide recovery and alert delivery, later-phase records, or a full RG1 release. The application and CI evidence above answer separate questions: `a4b2368…` is the source-bound local application qualification; `a2e73c7…` is the published feature/documentation checkpoint; run `37346137016` is the exact-head GitHub CI readback for that checkpoint. None of these claims that the pending exact corrected-head publication rereview is complete.

Production effects remain disabled. RG0 and RG1 are **HELD**. P2–P7 remain gated/planned, optional P6 remains disabled, and `main` remains at `8e50d68efbf2bec2aabfb2fe3226e01635de2adb` without this feature merged. Production is not fully integrated or released.

## Authoritative V4 documents

- [V4 production implementation blueprint](docs/v4/IMPLEMENTATION-BLUEPRINT.md) — target architecture, implementation state, phases, requirement mappings, rollback, and release gates.
- [Execution progress and checkpoints](docs/v4/progress.md)
- [P0 release ledger](docs/v4/p0-release.json)
- [P1 qualification matrix](docs/v4/p1-qualification-matrix.json)
- [Legacy dispatch contract](docs/v4/legacy-dispatch-contract.md)
- [Application qualification evidence](docs/v4/legacy-publication-evidence.json)
- [Inventory and preservation record](docs/v4/inventory.json)

## Historical and separate qualification records

These records retain their own local-fixture or repository-readback scope; they do not establish production release or CI for this README update.

- [P1 item-7 fixture qualification evidence](docs/v4/item7-evidence.json) — historical qualification at `e49a214`, 132 focused / 507 full non-live tests, implemented-fixture scope.
- [P1 item-7 independent-review summary](docs/v4/item7-independent-review.md) — the same historical fixture-review scope.
- [Parallel-agent operating contract](docs/v4/parallel-agent-contract.md) — a separate callable-agent extension and signed fixture lane.
- [Parallel-agent qualification evidence](docs/v4/parallel-agents-evidence.json) — separate local fixture qualification at `2653d2e`, 45 focused / 540 full non-live tests.
- [Repository-enforcement readback](docs/v4/repository-enforcement-evidence.json) — historical published-base evidence; it does not establish status for this README update.

## Verification and navigation

Run the documentation validator and its built-in tests:

```bash
python3 docs/v4/check.py --self-test
python3 docs/v4/check.py
```

The documentation gate covers 75 mapped obligations, preservation and drift checks, and local links. A protected-main merge also requires exact-head documentation (`verify`) and non-live regression (`p0-regression`) checks plus independent PR approval; the earlier checkpoint readback above does not apply to a later commit.

Existing fixture verification commands remain available:

```bash
bash scripts/verify-phase-9.sh
bash scripts/verify-phases-7-8.sh
bash scripts/verify-browser-evidence.sh
```

The pinned Playwright headless runtime is provisioned locally for tests; a fresh checkout still needs browser installation. Default Compose is for private fixtures; `compose.p0.yaml` is an effects-disabled, isolated qualification profile, not production enablement. Use the blueprint's release gates before exposing ingress or real accounts.

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
