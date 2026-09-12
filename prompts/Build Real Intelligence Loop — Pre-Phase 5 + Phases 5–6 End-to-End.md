Continue from the completed and verified Phases 1–4 foundation.

Treat `docs/core`, the current repository architecture, ADRs, contracts, `docs/implementation-progress.md`, and the existing verification suites as authoritative project context.

Do not redesign or duplicate the existing foundation.

The next objective is to turn the fixture-backed foundation into the first **real autonomous intelligence loop**:

`niche -> real research/signals -> market/audience understanding -> ranked topic opportunities -> strategy -> strategic packaging -> evidence -> selected content brief`

Complete the compatibility/trust gate plus Phases 5 and 6 end-to-end.

Do not implement full scripting, media generation, publishing, analytics or learning yet.

Use:

`Understand -> Spec -> Verifier -> Environment -> Implement -> Verify -> Inspect -> Repair -> Re-verify -> Checkpoint -> Repeat`

Work autonomously. Do not stop for approval on normal reversible engineering decisions.

## Start from the actual repository state

The existing foundation already provides PostgreSQL canonical state, Temporal durable execution, object-storage contracts, governance, budgets, policies, audit/provenance, callable Lead/Research/Strategy fixture agents, model/MCP/A2A boundaries, scoped memory, and niche bootstrap.

Preserve those contracts.

Do not replace working Phase 1–4 infrastructure merely because another implementation is possible.

Before running storage-heavy Docker verification, inspect available disk space. The previous full local rerun exhausted host storage through Temporal test volumes. Do not use broad destructive Docker cleanup without authorization. Prefer project-scoped cleanup, isolated test stacks, existing verified services, and targeted verification where appropriate.

---

# Pre-Phase-5 compatibility and trust gate

Before real external research is allowed, productionize the boundaries that Phase 1–4 intentionally left as fixtures.

## MCP

The current repository fixture targets `2025-11-25`.

Research and adopt the current stable MCP compatibility path.

As of this build direction, the current protocol is `2026-07-28` and the official Python SDK v2 supports that revision while negotiating older protocol generations.

Prefer the official maintained SDK rather than extending the handwritten fixture into a second MCP implementation.

Keep the project-owned MCP gateway contract.

Support current protocol discovery/calling and retain backward compatibility only where useful and cheap.

Record:

`protocol version -> SDK version -> supported extensions -> auth behavior -> compatibility status`

Do not leak MCP SDK types into canonical tool or agent domain entities.

Do not implement every optional MCP extension simply because it exists. Implement only what current research/tool usage requires.

## A2A

The repository fixture currently targets A2A `0.3.0`.

Upgrade the external-agent compatibility boundary to current A2A `1.0`.

Preserve the owned `RemoteAgentAdapter`.

Where practical, keep a compatibility/migration test proving old `0.3` descriptors fail clearly or can coexist intentionally rather than being silently misinterpreted.

Use the current official specification/SDK if it reduces maintenance and passes the reuse/build gate.

Do not make A2A mandatory for local agents.

## Agentic trust boundary

Real web research introduces hostile input.

Before giving agents unrestricted external research, establish a minimal production trust model.

Treat these as untrusted by default:

`web pages, search results, comments, repositories, documents, feeds, retrieved external text and remote tool output`

External content is data, not system instruction.

Introduce or complete canonical metadata for:

`trust_level`
`source_identity`
`effect_classification`
`delegated_authority`
`tool_scope`
`network_scope`
`memory_write_authority`
`provenance`

An external source must never be able to escalate privileges, grant itself tool access, override system policy, or directly create trusted durable memory.

The Lead Agent may propose durable memory derived from research, but runtime policy controls the write.

Maintain least privilege for every child agent/tool call.

---

# Production model execution

Turn the existing model boundary into a usable runtime while retaining deterministic fixtures for CI.

The system must continue to work with:

- deterministic test provider;
- OpenAI-compatible HTTP endpoints;
- custom/static gateways;
- future provider adapters.

Models are selected by capability/policy rather than being part of agent identity.

Support structured outputs with schema validation.

Capture provider/model/version, input/output identity, latency, tokens/usage where available, estimated/actual cost, failure status and parent agent run.

A schema-invalid model result must not silently become canonical workflow state.

At least the Lead, Research and Strategy agents must be able to use a real configured model through the gateway.

Do not hard-code one commercial vendor.

---

# Research access architecture

Use this access preference:

`official API/feed -> direct HTTP -> maintained connector -> deterministic browser -> adaptive browser agent`

Do not scrape with a browser when a reliable API/feed exists.

Do not bypass CAPTCHAs, access controls, paywalls or platform restrictions.

Create canonical contracts for research sources rather than source-specific domain models.

A source connector should expose at least:

`source_id`
`connector_version`
`source_type`
`fetch operation`
`cursor/window`
`raw identity`
`published/fetched time`
`canonical URL/resource identity`
`trust metadata`
`rate-limit metadata`
`provenance`

Select a small set of useful initial research sources after current ecosystem research.

Prefer 2–3 diverse, legal and maintainable sources instead of implementing ten shallow connectors.

At least one should use a stable API/feed/direct HTTP path.

Add browser research only where it provides real value.

## Browser research

Phase 4 deliberately had no browser research. Phase 5 should introduce the governed browser capability.

Use Playwright or another verified maintained deterministic browser layer as the default.

Expose it behind the existing Browser/Tool contract.

Support isolated contexts, domain/network policy, timeout/step limits, download controls, trace/screenshot evidence and read-only effect classification.

A semantic `browser_research_agent` may use it for unfamiliar pages.

Recurring stable flows should later be promoted into deterministic connectors.

Browser research must preserve source URL, fetch time, artifact hashes and tool/agent lineage.

---

# Phase 5 — Signals, research and strategy

Build the real signal/research pipeline without changing the canonical Phase 1–4 identities.

## Signal model

Create a provider-neutral canonical signal representation.

Preserve original/raw source references rather than flattening away lineage.

Signals should support measurable features such as:

`freshness`
`velocity`
`engagement where available`
`source diversity`
`niche relevance`
`audience relevance`
`saturation`
`novelty`
`confidence`
`risk`
`source trust`

Not every source provides every field. Missing data must remain explicit rather than fabricated.

## Normalization and deduplication

Normalize different source observations into the canonical signal contract.

Implement semantic/identity deduplication so the same event repeated across sources does not appear as ten unrelated ideas.

Retain every supporting source mapping even when signals merge.

## Topic opportunities

Signals are evidence; they are not automatically topics.

Create durable topic/opportunity candidates that contain:

`topic`
`why now`
`target audience`
`supporting signals`
`source diversity`
`expected interest`
`saturation`
`novelty`
`risks`
`deterministic score`
`AI judgement`
`explanation`

Use deterministic measurable scoring plus bounded AI semantic judgement.

Do not make an opaque LLM score the only ranking mechanism.

Store rejected opportunities where useful for later analysis.

## Research Agent

Replace the fixture-only behavior with real bounded research behavior through the canonical agent contract.

The Research Agent must remain directly callable by the end user and callable by the Lead Agent through the exact same run interface.

It should be able to:

research a niche;
investigate one topic;
compare competitors/public content;
find supporting/contradicting evidence;
identify unresolved questions;
return structured source-linked findings.

It must not automatically write permanent strategy or publish anything.

## Strategy Agent

The Strategy Agent consumes niche/program context plus research/signals and produces a versioned strategy proposal.

It should update or propose:

audience understanding;
positioning;
content pillars;
channel priorities;
topic priorities;
content cadence direction;
format opportunities;
success metrics;
known assumptions;
known uncertainties.

The Lead Agent decides whether to accept a new StrategyVersion.

Do not silently overwrite previous strategy versions.

## Scheduling

Add durable research scheduling only where useful.

A scheduled run should create normal canonical agent/tool runs rather than a hidden second execution system.

Keep cadence configurable.

Do not create a huge crawler fleet in this phase.

### Phase-5 outcome

From a real or test niche, the system should be able to produce:

```text
Niche
  ↓
bounded real research
  ↓
normalized source-linked signals
  ↓
deduplicated opportunities
  ↓
ranked topics
  ↓
versioned strategy update
  ↓
initial prioritized content queue/calendar
```

Everything must remain explainable and traceable.

---

# Phase 6 — Strategic packaging, evidence and content brief

Once a topic is selected, create the system that decides **how the idea should be presented before full scripting**.

Do not implement full script generation yet.

## Strategic package

For one topic, generate several materially different candidates.

Each package should contain at least:

`target audience`
`audience problem/desire`
`angle`
`hook/opening`
`promise/payoff`
`format`
`expected duration/size`
`opening visual or creative concept`
`novelty`
`evidence requirements`
`risk notes`

Do not generate ten nearly identical hooks and call them ten packages.

Diversity should be measurable enough to reject trivial duplicates.

## Package evaluation

Use deterministic constraints plus semantic judgement.

Evaluate areas such as:

audience fit;
clarity;
novelty;
curiosity;
promise strength;
credibility;
evidence availability;
saturation;
brand/strategy fit;
risk;
clickbait/misleading risk.

Keep all candidates and evaluation records.

The selected candidate must record why it was selected.

## Evidence workspace

Research the chosen package before scripting.

Create canonical evidence and claim records.

An evidence item should preserve:

source identity;
URL/resource identifier;
publisher/author when known;
published/fetched time;
content hash;
source trust;
relevant excerpt/reference;
research agent/tool lineage;
usage/rights notes where relevant.

A claim should explicitly link to:

supporting evidence;
contradicting evidence;
verification status;
confidence;
freshness requirement.

External text does not become truth merely because the model retrieved it.

## Originality and saturation

Before approving a package, compare it against:

recent program content;
existing candidate packages;
retrieved public-topic saturation;
obvious template repetition.

Build the contract now so later analytics can learn whether originality correlates with performance.

Do not optimize toward copying competitor phrasing or structure.

## Content brief

The Phase-6 primary artifact is a canonical versioned `ContentBrief`.

It should combine:

topic/opportunity;
selected strategic package;
audience;
intent;
promise;
format;
creative direction;
required claims;
evidence;
prohibited/unsupported claims;
source references;
brand/strategy references;
known risks;
open research questions;
recommended next action.

This brief is the future Phase-7 input.

Phase 7 should not need to reconstruct why the topic/package was chosen.

---

# Required durability

Research and packaging are agent workflows, so they must inherit the Phase 1 durable-runtime guarantees.

A Lead Agent run may delegate Research, Strategy and browser work.

Parent/child lineage must survive restart.

Long research jobs must resume or retry safely.

Tool/provider calls must respect timeout, budget and permission controls.

Cancellation must propagate sensibly to child runs.

A source fetch retry must not create duplicate canonical records.

---

# Required memory behavior

Use the existing PostgreSQL-scoped memory rather than introducing a second memory system.

Add vector/semantic retrieval only if measured Phase-5/6 requirements justify it.

Every durable memory write derived from external information should carry source and trust metadata.

Where applicable retain:

`created_at`
`verified_at`
`valid_until`
`confidence`
`trust_level`
`supersedes`
`contradicts`
`evidence_refs`
`writer_actor`

Do not push all memory into every model call.

Retrieve only task-relevant context.

---

# Required evaluation

Introduce the first real content-intelligence eval suite.

Build a small golden fixture set for:

research grounding;
signal normalization;
deduplication;
topic ranking;
package diversity;
package-selection consistency;
claim/evidence linkage;
schema validity;
memory isolation;
prompt-injection resistance.

Use deterministic assertions whenever possible.

LLM-as-judge may supplement verification but must not be the only verifier.

Keep real-network/model smoke tests separate from deterministic CI so normal CI remains reproducible.

---

# End-to-end verifier

The final verifier for this build should demonstrate approximately:

```text
clean or isolated runtime
      ↓
create niche/program
      ↓
Lead Agent starts durable intelligence run
      ↓
Research Agent called through canonical contract
      ↓
API/feed/HTTP research
      ↓
optional browser research
      ↓
source records + evidence persisted
      ↓
signals normalized
      ↓
duplicates merged
      ↓
topic opportunities produced
      ↓
deterministic + semantic ranking
      ↓
Strategy Agent updates strategy
      ↓
selected topic
      ↓
multiple strategic packages generated
      ↓
packages independently evaluated
      ↓
chosen package
      ↓
additional evidence/claim validation
      ↓
versioned ContentBrief produced
```

Verify complete lineage from `ContentBrief` back through:

`package -> topic -> signal -> source/evidence -> tool/model runs -> specialist runs -> Lead Agent run -> content program`

Also test restart/resume during research, invalid model output, unavailable provider, source timeout, duplicate signals, contradictory evidence, budget exhaustion, permission denial, prompt-injection fixture, malformed browser result and cancellation.

---

# Protocol migration verifier

Before declaring this build complete, prove the new interoperability boundaries.

For MCP, verify the current stable protocol path through the official maintained implementation while preserving the project-owned gateway. If backward compatibility with `2025-11-25` is retained, prove negotiation rather than maintaining two unrelated implementations.

For A2A, verify the current `1.0` compatibility path. Keep `0.3` only as a deliberate compatibility/migration case if useful.

Update architecture documentation and compatibility matrices accordingly.

---

# Environment constraint

The previous build reported host disk exhaustion during a later full Docker rerun.

At the start:

inspect filesystem and Docker disk usage;
reuse already verified infrastructure where safe;
avoid unbounded image/volume duplication;
use project-scoped disposable stacks;
do not perform destructive global Docker cleanup without authorization.

If insufficient storage genuinely prevents required integration verification, complete all safe work and report the exact remaining verifier and required capacity rather than pretending it passed.

---

# Documentation

Update project-local documentation to reflect the implementation actually delivered.

Document:

real research architecture;
source connector contracts;
browser research boundary;
trust model;
MCP compatibility;
A2A compatibility;
model execution;
signal/topic schemas;
strategy lifecycle;
package lifecycle;
evidence/claim model;
ContentBrief;
memory-write rules;
verification commands;
known limitations;
Phase-7 handoff.

Do not copy speculative future features into "implemented" documentation.

---

# Completion criteria

This build is complete when the existing Phases 1–4 guarantees still pass and the project can additionally demonstrate:

`niche -> real research -> source-linked signals -> ranked opportunities -> strategy -> multiple strategic packages -> evidence -> selected versioned ContentBrief`

The Lead Agent remains accountable.

Research and Strategy specialists remain independently callable.

The same canonical contracts work for direct and delegated invocation.

Current MCP and A2A compatibility is verified.

Untrusted research cannot directly become privileged instruction or trusted memory.

Every important decision remains traceable.

No full scripting, media production, live publishing, analytics optimization or learning loop should be added merely to make the demo look more complete.

When finished, provide one implementation report containing the architecture changes, dependency/ADR decisions, protocol migrations, source connectors, agent behavior, schemas/migrations, verification evidence, exact reproduction commands, known limitations, and the precise Phase-7 starting point.

Do not ask for approval between the compatibility gate, Phase 5 and Phase 6 unless genuinely blocked by credentials, a destructive environment action, policy authorization or missing business intent.