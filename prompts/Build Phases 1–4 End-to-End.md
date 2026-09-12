Continue building this project using `docs\core\` as the authoritative product direction, architecture constraints and acceptance contract.

Complete **Phases 1 through 4 end-to-end**.

Do not stop after planning, ADRs, a spec, scaffolding, or one phase. Work through all four phases sequentially, verifying and checkpointing each phase before moving to the next.

Do not waterfall all four phases into one massive unverified change. Use the project engineering loop:

`Understand → Spec → Verifier → Environment → Implement → Verify → Inspect → Repair → Re-verify → Checkpoint → Repeat`

Choose the largest coherent slice that remains independently testable and reversible.

## Current environment and proposed foundation

The repository currently contains mainly the architecture/build pack.

The available local environment includes:

- Docker 29
- Python 3.13

The current proposed Phase-1 stack is directionally approved:

- PostgreSQL as canonical system of record;
- Temporal as the candidate durable workflow/runtime;
- an S3-compatible object-storage implementation, with SeaweedFS currently proposed;
- Python/FastAPI control API;
- Python worker/runtime services;
- SQLAlchemy + Alembic for persistence/migrations;
- schema validation using appropriate maintained Python tooling;
- OpenTelemetry-compatible tracing/observability.

However, these are **implementation candidates, not permanent architecture**.

Before adopting them, verify their current stable versions, Python 3.13 compatibility, license, security posture, maintenance activity, Docker/self-hosting quality and operational fit.

If Temporal, SeaweedFS or another proposed component fails those checks, choose a stronger maintained free/self-hostable alternative behind the same canonical interface and document the decision.

Do not pause to ask me whether Temporal, SeaweedFS, FastAPI or another ordinary reversible dependency is acceptable if your verification supports the choice.

Create a concise ADR for each major dependency decision and proceed.

Do not hand-build a workflow engine, queue system, S3 replacement, OAuth stack, tracing system or similar mature commodity infrastructure unless the build-vs-reuse analysis clearly proves custom ownership is the better long-term option.

## Scope boundary

Implement only what is required to make **Phases 1–4 complete and genuinely integrated**.

Do not begin the full Phase-5 signal intelligence pipeline, strategic packaging, scripting, creative generation, publishing or analytics system.

Small mocks, fixtures, minimal connectors or thin research capabilities are allowed when needed to prove Phases 1–4.

The result at the end of Phase 4 must already feel like one working system rather than four disconnected foundations.

---

# Phase 1 — Deterministic substrate and canonical data

Build the reliable platform underneath the AI/agent layer.

Implement:

- PostgreSQL canonical data model and migrations;
- workspace identity;
- content-program identity;
- canonical IDs independent from external providers;
- object-storage abstraction;
- durable workflow/job/checkpoint abstraction;
- queue/task execution;
- schedules/timers;
- bounded retries and backoff;
- timeouts;
- dead-letter/error state;
- cancellation;
- resume/replay primitives;
- idempotency keys;
- external-effect reconciliation;
- audit events;
- provenance records;
- distributed run/trace correlation IDs;
- secret references;
- permission scopes;
- effect classifications;
- policy/approval framework;
- dry-run mode;
- budgets;
- estimated/reserved cost;
- actual cost ledger;
- rate/resource limits;
- control/admin API;
- health/readiness;
- versioned capability/plugin registry;
- plugin compatibility metadata;
- provider/protocol version metadata;
- adapter contract-test harness.

Keep the following future requirements represented in the canonical model without attempting to implement their entire future subsystems yet:

- `trust_level`;
- delegated authority / permission scope;
- `workspace_id`;
- `content_program_id`;
- optional `tenant_id` boundary if the architecture can support it cheaply without prematurely building SaaS tenancy;
- `policy_version`;
- `domain_policy_ref`;
- data classification;
- retention/deletion metadata;
- provenance/content-origin extension fields;
- future C2PA linkage;
- OpenTelemetry-compatible trace identities;
- provider/tool/agent protocol versions;
- budget reservation;
- actual-cost reconciliation.

Keep provider-specific objects outside canonical entities.

### Phase-1 verifier

Prove with automated tests that a representative long-running dummy workflow can:

1. start;
2. persist durable state;
3. checkpoint;
4. perform a mocked external side effect with an idempotency key;
5. have the worker/process terminated;
6. resume from durable state;
7. reconcile the external effect instead of duplicating it;
8. respect a permission/policy check;
9. reserve and reconcile a budget;
10. emit audit, provenance and trace records;
11. finish successfully.

Actually perform the interruption/restart test. Do not replace it with mocked assertions that never kill/restart the worker.

If using Temporal, keep it behind the canonical `WorkflowBackend` boundary. Temporal types must not leak into core business/domain objects.

If using SeaweedFS or another object store, keep it behind the canonical object-storage contract. S3/vendor-specific types must not become domain objects.

Checkpoint Phase 1 only after this verifier passes.

---

# Phase 2 — Canonical callable-agent system

Build the framework-neutral agent system described by the pack.

The product must distinguish:

`Agent identity ≠ Model identity ≠ Framework identity ≠ Process identity`

Implement canonical entities/contracts for at least:

- `AgentManifest`;
- `AgentVersion`;
- `AgentRun`;
- `AgentDelegation`;
- `TeamManifest`;
- `TeamVersion`;
- agent events;
- agent artifacts/results;
- parent/child lineage.

The canonical agent manifest must support the pack's concepts:

- agent ID/version;
- display name/description;
- skills/capabilities;
- typed input schema;
- typed output schema;
- tool scopes;
- memory scopes;
- required secret scopes;
- model policy;
- effect classification;
- budget;
- timeout;
- sync/async support;
- health/status.

Implement the stable Lead Content Agent identity, but do not yet build the full content-generation intelligence planned for later phases.

Implement a small initial specialist set sufficient to prove the platform, preferably:

- `research_agent`;
- `strategy_agent`.

These may initially share the same underlying model/runtime.

Do not create a separate AI deployment merely because an agent has a different role.

Implement canonical invocation surfaces:

- list agents;
- describe agent;
- run agent;
- get run;
- inspect run events;
- cancel run;
- resume where supported.

At minimum provide:

- REST API;
- CLI;
- small Python SDK.

Keep the contract simple enough that a future TypeScript SDK can map directly onto it.

The Lead Agent must invoke a specialist through **the exact same canonical run contract** an end user uses.

Support synchronous calls for short tasks and durable asynchronous runs for long tasks.

Support reusable teams/crew compositions without making member agents inaccessible individually.

### Phase-2 verifier

Demonstrate that:

1. the user can list enabled agents;
2. the user can describe `research_agent`;
3. the user can invoke it directly;
4. the Lead Agent can invoke that same specialist through the same contract;
5. parent/child lineage is preserved;
6. outputs use canonical structured result/artifact types;
7. one long-running agent call survives worker restart;
8. budget/timeout/cancel controls apply;
9. disabling an optional specialist does not corrupt the canonical history;
10. a team composition can invoke multiple agents while each remains directly callable.

No provider/framework SDK types may appear in canonical API responses or persisted agent domain entities.

Checkpoint Phase 2 only after these tests pass.

---

# Phase 3 — Model, framework, tool and remote-agent adapters

Now connect the canonical agent platform to interchangeable execution systems.

## Model Gateway

Implement the core-owned `ModelGateway`.

Agents request capabilities/policies rather than embedding provider names into their domain identity.

Support:

- custom static endpoints;
- OpenAI-compatible endpoints;
- provider adapter registration;
- model capability metadata;
- structured-output validation;
- model/runtime selection;
- timeout/retry;
- fallback policy;
- token/cost/latency capture;
- model provenance.

Do not make LiteLLM or any provider SDK the canonical interface. LiteLLM may be used behind the gateway if current verification shows it is the best fit.

Provide a deterministic fake/test provider so the complete test suite runs without paid credentials.

Where practical, also support one real configurable OpenAI-compatible adapter.

The system must be able to route the same logical specialist through at least two eligible execution configurations without changing the specialist's canonical identity or upstream/downstream domain code.

## Agent-framework adapters

Do not adopt an agent framework merely to say the system supports one.

First make the canonical internal runner work.

Then add framework adapters only where they provide concrete value.

CrewAI, OpenAI Agents SDK, LangGraph or another maintained framework may be supported, but their native Agent/Task/Crew classes must stay behind an adapter.

At least one framework/runtime swap path should be proven through contract tests.

## MCP

Implement an MCP gateway/adapter for appropriate tools/resources.

Use MCP for vertical tool/resource access, not as the canonical identity model for local agents.

Because protocol specifications evolve, verify the **current MCP specification and extensions at implementation time** rather than copying an obsolete pinned version from old notes.

Store supported MCP protocol/extension compatibility metadata.

Prove:

- tool discovery;
- schema retrieval;
- bounded tool invocation;
- provenance;
- permission scope;
- timeout/error handling.

A simple local specialist may optionally be projected as an MCP tool, but this is convenience only.

## A2A

Implement the external-agent adapter using the current compatible A2A specification.

Use A2A for independent remote/opaque agents rather than forcing it on ordinary local specialists.

Create a local/fixture remote A2A agent so tests do not require a commercial remote service.

Prove:

- discovery/Agent Card or current equivalent;
- skill/capability inspection;
- task invocation;
- asynchronous/stateful result handling where supported;
- artifact/result mapping into canonical records;
- parent/child provenance;
- clean failure on incompatible protocol version.

Do not leak A2A protocol objects into core agent entities.

### Phase-3 verifier

Prove end-to-end that:

1. one canonical specialist runs through execution configuration A;
2. the same specialist runs through configuration B;
3. no domain code changes between those runs;
4. MCP discovery and a tool call work;
5. the tool result has audit/provenance;
6. a fixture remote A2A agent can be discovered;
7. it can receive a task;
8. its result/artifact maps back into canonical records;
9. removing one framework/model adapter leaves agent history readable;
10. incompatible adapter/protocol versions fail clearly rather than silently corrupting state.

Checkpoint Phase 3 only after all applicable contract/integration tests pass.

---

# Phase 4 — Niche bootstrap and scoped memory

Now produce the first meaningful product experience:

```text
Niche + optional constraints
        ↓
durable content program
        ↓
initial research
        ↓
audience understanding
        ↓
initial strategy
        ↓
channels/content pillars
        ↓
success metrics
        ↓
scoped durable memory
```

The intended UX is approximately:

```text
User:
  Niche: Personal Finance

System:
  understand niche
  research enough context to bootstrap
  infer provisional audience
  infer provisional brand/positioning defaults
  suggest channels
  establish content pillars
  establish success metrics
  save an explainable strategy
  initialize memory
```

Do not require the user to configure internal agents.

Ask the user only when genuinely blocked by:

- missing credentials required for the chosen operation;
- irreversible business decisions;
- policy authorization;
- material ambiguity that cannot be safely represented as a provisional assumption.

For ordinary strategy choices, make a reasonable provisional decision, record the assumption and continue.

## Bootstrap research

Phase 4 needs enough research to make the initial program explainable, but **do not accidentally implement the full Phase-5 signal engine**.

Implement a bounded bootstrap-research capability using the Research Agent and available safe read-only tools/connectors.

It may use:

- search/API;
- direct HTTP;
- approved browser research if already available;
- seeded fixtures for tests.

Store source identity, fetch time and provenance for externally researched facts.

Do not yet build:

- large scheduled source fleets;
- continuous trend ingestion;
- broad signal normalization;
- advanced topic-ranking pipelines;
- production social monitoring.

Those belong to Phase 5.

## Memory

Use PostgreSQL as the source of truth.

Do not add a vector database simply because this is an AI system. Add vector/semantic indexing only if the Phase-4 retrieval requirements justify it.

Implement scoped memory categories compatible with the pack:

- working/session memory;
- semantic niche/brand/audience/strategy memory;
- evidence/source memory;
- episodic decision/history memory;
- analytics/experiment memory placeholder;
- artifact memory/reference.

Memory retrieval must be scoped.

Do not dump all stored context into every agent call.

Include future-safe metadata where practical:

- source;
- trust level;
- confidence;
- created time;
- verified time;
- validity/expiry;
- supersedes/conflicts;
- writer/actor identity;
- evidence references;
- sensitivity/data classification.

External retrieved content must not automatically receive trusted durable-memory-write authority.

The Lead Agent decides what should become durable program knowledge, subject to runtime policy.

## Strategy output

Create a versioned `StrategyVersion` or equivalent containing at least:

- niche;
- assumptions;
- target audiences;
- major audience needs/problems/desires;
- positioning;
- preliminary content pillars;
- recommended initial channels;
- content-format direction;
- preliminary success metrics;
- known uncertainties;
- source/evidence references;
- agent/model/tool provenance.

Create the initial content program/workspace and persist this result.

### Phase-4 verifier

Starting from a clean deployment, this must work:

```text
content program create \
  --niche "Personal Finance"
```

or an equivalent API/SDK call.

The system must then:

1. create a durable workspace/content program;
2. start/resume a Lead Agent run;
3. invoke the Research Agent through the canonical specialist contract where useful;
4. perform bounded bootstrap research or use the configured test fixture;
5. preserve source provenance;
6. derive provisional audience knowledge;
7. create a versioned initial strategy;
8. establish content pillars/channels/success metrics;
9. initialize scoped durable memory;
10. record assumptions and uncertainties;
11. return an explainable result to the caller.

The run must be restart-safe.

The same Research Agent must remain directly callable independently of the Lead Agent.

---

# Cross-phase invariants

These apply to all four phases.

### Core owns identity

Third-party systems may have their own IDs, but they map to our canonical IDs.

Never make Temporal, SeaweedFS, CrewAI, LiteLLM, MCP, A2A or a model provider the system of record for core identities.

### Durable execution

Any work that can take a long time, wait on a dependency, require approval, retry, or survive restart must not depend on one Python process remaining alive.

### Typed boundaries

Agent/tool/workflow/plugin interfaces use validated schemas.

Free-form model output must not silently become workflow state.

### Agent identity is independent

Changing model/provider/framework must not create a new logical agent unless the agent definition/version itself changed.

### Least privilege

Agent and tool runs receive only the memory, secrets, tools, permissions and effect scope needed for that task.

### Dry-run first

No live publishing, purchases, destructive actions or unrelated real-account effects are needed for Phases 1–4.

### Provenance

Persist enough lineage to answer:

```text
What happened?
Who/what initiated it?
Which agent/version?
Which model/runtime?
Which tools?
Which sources?
Which policy?
Which parent run?
Which artifacts?
What did it cost?
What changed?
```

### Cost control

Track:

- estimated cost;
- reserved cost;
- actual cost;
- run/provider/tool attribution.

Do not allow unbounded loops.

### Future production hooks

Do not fully implement later production systems now, but avoid architectural dead ends for:

- agentic trust/security;
- domain/jurisdiction policy packs;
- C2PA/content provenance;
- data-retention/deletion;
- multi-tenant isolation;
- platform-specific policy;
- formal evals;
- experiment engine;
- publishing;
- analytics.

### Reuse first

Before implementing a substantial subsystem, search current maintained open/free/self-hosted options.

Record:

- candidates;
- exact version/commit;
- license;
- security posture;
- operational cost;
- reason selected;
- adapter boundary;
- replacement/exit path.

Do not choose from GitHub stars alone.

---

# Required project documentation

While building, create/update concise project-local documentation for:

- current architecture;
- dependency inventory;
- ADRs;
- canonical contracts;
- API/CLI usage;
- database/migration model;
- workflow/runtime behavior;
- agent manifests;
- model gateway;
- MCP/A2A compatibility;
- memory model;
- local development;
- Docker deployment;
- test/verification commands;
- restart/recovery procedure;
- known limitations;
- remaining Phase-5+ work.

Document the exact dependency versions actually used.

---

# Final Phase-1–4 end-to-end verification

Do not declare completion merely because each module has unit tests.

Run a clean integrated verifier from the real Docker stack.

At minimum:

```text
fresh Docker environment
        ↓
start PostgreSQL/object storage/workflow runtime/API/workers
        ↓
run migrations
        ↓
health checks pass
        ↓
start durable dummy job
        ↓
kill worker mid-run
        ↓
restart worker
        ↓
job resumes
        ↓
mock external effect is NOT duplicated
        ↓
list callable agents
        ↓
invoke research_agent directly
        ↓
invoke same research_agent from Lead Agent
        ↓
run representative specialist via two runtime/model configurations
        ↓
discover + call MCP fixture tool
        ↓
discover + invoke A2A fixture agent
        ↓
create content program from niche only
        ↓
bootstrap research
        ↓
create initial strategy
        ↓
persist scoped memory
        ↓
inspect lineage/provenance/audit/cost/trace
```

Also test:

- cancellation;
- timeout;
- retry exhaustion;
- permission denial;
- budget denial;
- schema-invalid model output;
- incompatible adapter version;
- disabled provider;
- missing optional specialist;
- container restart;
- database migration on clean environment.

All critical automated tests must pass.

Do not hide failing tests, mark required tests skipped, or replace a real restart/reconciliation test with mocks merely to obtain green CI.

---

# Completion criteria

Phases 1–4 are complete only when a fresh Linux/Docker deployment can demonstrate:

**Durability**
- workflows resume after failure;
- retries are bounded;
- external effects are idempotent/reconciled.

**Canonical platform**
- PostgreSQL is the core source of truth;
- artifacts use a replaceable object-store abstraction;
- plugin/protocol contracts are versioned.

**Callable multi-agent system**
- Lead Agent has stable identity;
- specialists are discoverable and directly callable;
- direct and delegated invocation use the same contract;
- sync/async/cancel/status/events work.

**Provider/framework independence**
- one logical specialist works through at least two eligible execution configurations;
- model/framework/provider SDK objects do not pollute domain data.

**Protocol/tool integration**
- MCP fixture works through the tool gateway;
- A2A fixture works through the remote-agent gateway.

**Niche-only product path**
- a niche alone can bootstrap a durable content program and explainable initial strategy;
- internal agent configuration is not required from the user.

**Memory**
- memory is durable, scoped and provenance-aware;
- agents retrieve only relevant context.

**Governance**
- permissions, effect classes, policies, approvals and budgets are enforced deterministically.

**Observability**
- audit, provenance, traces, parent/child lineage and costs can reconstruct the run.

**Operations**
- Docker deployment, migrations, health checks and restart/recovery procedure are documented and tested.

When these criteria are satisfied, provide a final implementation report containing:

1. architecture actually implemented;
2. dependency/ADR decisions;
3. repository tree of important components;
4. migrations/entities created;
5. canonical interfaces/contracts;
6. Phase-1 verifier results;
7. Phase-2 verifier results;
8. Phase-3 verifier results;
9. Phase-4 verifier results;
10. final integrated E2E test evidence;
11. commands needed to reproduce the tests;
12. known limitations;
13. deliberate Phase-5+ exclusions;
14. any verified architecture changes discovered during implementation.

Do not ask for approval between Phases 1, 2, 3 and 4.

If implementation evidence proves one of the current assumptions wrong, update the relevant spec/ADR and continue from the verified understanding.

Only stop and ask me when genuinely blocked by credentials, an irreversible production decision, required policy authorization, or missing business intent that cannot safely be represented as a provisional assumption.

The goal is not to generate the maximum amount of code.

The goal is to finish **one coherent, durable, framework-neutral Phases-1–4 foundation that actually works end-to-end and is ready for Phase 5 without architectural rework.**
