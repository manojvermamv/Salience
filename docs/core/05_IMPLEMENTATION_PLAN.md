# Implementation Plan

Adapt this plan to the existing repository. Do not create a second architecture if equivalent foundations already exist.

## Phase 0 — Inspect, research and prove assumptions

- inventory current code, schemas, services, deployment and tests;
- map existing pieces to the target architecture;
- verify current versions/licenses/security/APIs of candidate dependencies;
- search for proven open/free/self-hosted solutions before writing commodity infrastructure;
- compare adapter vs upstream plugin vs library composition vs light fork vs custom build;
- verify CrewAI/OpenAI Agents/Temporal/A2A/MCP and other current alternatives before choosing runtime components;
- create short ADRs with lifecycle cost and replacement/exit path;
- establish local Docker/CI baseline.

Deliverable: current-state map, dependency decisions and runnable dev stack.

## Phase 1 — Deterministic substrate and canonical data

Build the reliable envelope first:

- PostgreSQL migrations and core identities;
- object-storage abstraction;
- durable workflow/job/checkpoint interface;
- queue/scheduling/timers;
- retries/backoff/timeouts/dead-letter handling;
- idempotency/reconciliation primitives;
- audit/provenance/tracing;
- secrets/permissions/budgets;
- policy/approval framework;
- control/admin API;
- dry-run mode;
- versioned plugin/capability registry and contract-test harness.

Evaluate Temporal or another mature durable-execution backend before hand-rolling this layer.

Deliverable: a dummy long-running job survives worker restart and resumes without duplicate external effects.

## Phase 2 — Canonical agent system

Implement the framework-neutral agent model before adopting one agent framework deeply:

- `AgentManifest`, `AgentVersion`, `AgentRun`, `AgentDelegation`;
- stable Lead Agent identity;
- callable specialist-agent registry;
- typed input/output, tool/memory/secret scopes, budgets and effect classification;
- sync and async invocation;
- API, CLI and small SDK surfaces;
- events/status/cancel/resume;
- `TeamManifest` / crew-style composition;
- same invocation contract for internal Lead-Agent calls and direct end-user calls.

Deliverable: the user can list agents and directly invoke a `research` specialist; the Lead Agent can invoke the same specialist contract internally.

## Phase 3 — Agent/framework/protocol adapters

- model gateway with custom static/OpenAI-compatible endpoints;
- framework adapters only where useful (CrewAI, OpenAI Agents SDK, others);
- MCP client/server adapter for tools/resources;
- A2A v1.x adapter for remote agent discovery/invocation;
- optional projection of simple local specialists as MCP tools;
- contract tests proving canonical behavior remains unchanged across implementations.

Deliverable: one specialist can run through two eligible model/runtime configurations without domain-code changes; a fixture remote A2A agent can be discovered and invoked.

## Phase 4 — Niche bootstrap and memory

From `niche + optional constraints`:

- create a durable content program/workspace;
- research initial audience/market/competitors;
- propose strategy, channels, content pillars and success metrics;
- derive provisional brand/audience defaults;
- ask the user only for genuinely blocking/irreversible choices;
- initialize scoped semantic/evidence/episodic/analytics memory.

Deliverable: a niche-only request creates an explainable strategy/program without manual internal-agent configuration.

## Phase 5 — Signals, research and strategy

- source connector contract;
- initial legal/reliable sources;
- normalization/deduplication/enrichment;
- browser/API research;
- deterministic measurable features;
- Lead-Agent semantic interpretation;
- Research/Strategy specialists usable independently;
- source lineage and evidence records;
- initial content calendar/priority queue.

Deliverable: scheduled research produces ranked, explainable topic opportunities and can be queried through a direct specialist call.

## Phase 6 — Strategic packaging, evidence and content brief

- audience/problem/desire analysis;
- angle/hook/promise/format/opening-visual candidates;
- package comparison/judgement;
- evidence collection and claim linking;
- content brief;
- originality/saturation checks;
- save rejected candidates for later analysis.

Deliverable: one topic produces multiple strategic packages, a selected package and a traceable brief/evidence pack.

## Phase 7 — Script/copy and creative production

- outline/draft/revision capabilities;
- fact/claim verification pass;
- brand/style adaptation;
- creative brief/storyboard;
- generic creative-job/asset contract;
- Higgsfield/HeyGen/Synthesia/Veo/open/local adapter evaluation;
- FFmpeg/editing/captions;
- multi-variant generation and comparison;
- async provider jobs/webhooks/polling;
- owned artifact storage and rights/provenance.

Specialist Writer/Creative/Production agents are callable but may share one underlying model.

Deliverable: an approved brief becomes a reproducible content asset package with complete lineage.

## Phase 8 — Distribution packaging and governance

- title/thumbnail/caption/description/SEO/metadata;
- channel adaptation and localization;
- duplicate/misleading-claim checks;
- copyright/rights and platform-policy checks;
- media technical validation;
- final pre-publish policy/approval gate.

Deliverable: one asset can produce valid platform-specific publication packages with explainable verifier results.

## Phase 9 — Publishing, analytics and learning

- canonical publication schema;
- official platform APIs and/or Postiz adapter;
- scheduling and idempotent reconciliation;
- metric snapshots at multiple horizons;
- attribution to topic/package/script/assets/distribution package/agent versions;
- experiment model;
- Lead-Agent interpretation of outcomes;
- versioned strategy/prompt/routing changes;
- keep/rollback logic.

Deliverable: sandbox publication -> analytics -> experiment -> next-cycle decision works end to end.

## Phase 10 — Browser/app and production hardening

- isolated browser/app workers;
- domain/action policies;
- encrypted auth profiles;
- failure screenshots/traces;
- adaptive-to-deterministic promotion;
- TLS/reverse proxy;
- backups/restore;
- secret rotation;
- health/readiness;
- rate/cost limits;
- monitoring/alerts;
- dependency/license/security review;
- upgrade/runbooks.

Deliverable: a clean Linux host can deploy, recover and run the supported autonomous loop.

## Build discipline

For each meaningful slice use:

`Understand -> Spec -> Verifier -> Environment check -> Implement -> Verify -> Inspect -> Repair -> Re-verify -> Checkpoint -> Repeat`

Use the largest coherent slice that remains independently testable and reversible. Do not waterfall the whole system and verify only at the end. Do not make the human specify every file/function when the agent can safely determine implementation details.

"Spec -> Verifier -> Environment" is used here as a **Karpathy-inspired engineering scaffold**, not as a claim that Karpathy published an official framework with exactly that name/order. See `09_CALLABLE_AGENTS_AND_ENGINEERING_METHOD.md`.

## Reuse/build decision rule

Before implementing a substantial commodity subsystem, record:

1. required capability;
2. current free/open/self-hosted candidates inspected;
3. adapter/plugin/library/fork/custom options;
4. license/security/maintenance/resource cost;
5. data ownership and exit path;
6. why custom ownership wins if building from scratch.

Do not fork by default.

## Final implementation rule

A complete vertical slice is better than many disconnected agents. Start with the Lead Agent plus a small set of genuinely useful callable specialists; add more only when their independent value is demonstrated.
