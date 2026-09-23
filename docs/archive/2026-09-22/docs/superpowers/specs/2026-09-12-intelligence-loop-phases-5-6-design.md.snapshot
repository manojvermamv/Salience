# Real Intelligence Loop: Pre-Phase 5 and Phases 5–6 Design

**Goal:** Extend the verified Phases 1–4 substrate with the first durable,
source-grounded intelligence loop: `niche -> research -> signals -> ranked
opportunities -> strategy -> packages -> evidence -> ContentBrief`.

**Status:** Approved by the owner's autonomous-build direction in
`prompts/Build Real Intelligence Loop — Pre-Phase 5 + Phases 5–6 End-to-End.md`.
No live provider credentials were supplied, so live integrations are configured
through environment-backed adapters and exercised in deterministic CI through
protocol-faithful fixtures. Real-network and real-model smoke tests remain
explicit opt-in commands.

## Scope and Boundaries

This build adds the compatibility/trust gate, real model execution boundary,
governed research access, Phase 5 signals/research/strategy, and Phase 6
strategic packaging/evidence/briefs. It preserves PostgreSQL as canonical
state, Temporal as the durable runtime, the public agent contract, scoped
memory, and existing audit/provenance/budget/policy infrastructure.

It does not add scripting, media generation, publishing, analytics,
optimization/learning, autonomous browser writes, credentialed browsing, or
general-purpose web scraping. Every external connector is read-only.

## Decision Summary

### Selected approach: SDK-first, owned-contract adapters

Use the official SDKs behind project-owned adapters and canonical DTOs:

| Boundary | Decision | Reason and exit path |
| --- | --- | --- |
| MCP | Adopt `mcp==2.2.0`, the maintained v2 SDK for MCP `2026-07-28`; retain the owned `ToolGateway` and negotiate the retained `2025-11-25` fixture path. | The SDK supports the modern protocol and older revision without domain-type leakage. Removing it leaves tool manifests/results and the gateway readable. |
| A2A | Adopt `a2a-sdk==1.1.2`, the current PyPI release implementing A2A `1.0` with `0.3` compatibility. | It replaces a handwritten protocol surface while `RemoteAgentAdapter` remains the project contract. Removing it leaves local agents independent. |
| Browser | Add optional `playwright==1.62.0` behind `BrowserResearchTool`. | Playwright provides isolated contexts, deterministic navigation, screenshots, and trace archives. Browser binaries are an operator-installed optional runtime, not CI's default dependency. |
| HTTP/feed research | Reuse existing `httpx`; use standard-library XML parsing for configured publisher RSS/Atom feeds and the official versioned Hacker News Firebase API. | These are small source adapters, not a crawler framework. They need no additional parser or provider-specific domain model. |
| Model runtime | Extend the existing static/OpenAI-compatible gateway and add structured invocation recording. | Agent identity remains independent of provider/model. A different HTTP-compatible or custom gateway can replace it. |

Alternatives rejected: extending the handwritten MCP/A2A fixtures into a second
protocol implementation (higher maintenance and compatibility risk), adopting a
full agent framework as the control plane (would duplicate the existing native
agent/runtime model), and a browser-first crawler (violates API/feed-first and
creates unnecessary hostile-input exposure).

All adopted dependencies are permissively licensed: MCP SDK is MIT, A2A SDK is
Apache-2.0, and Playwright is Apache-2.0. Exact versions, protocol support,
auth behavior, limitations, upgrade procedure, and replacement path are
recorded in ADRs and the compatibility matrix.

## Architecture

### 1. Compatibility and trust boundary

`McpSdkAdapter` and `A2ASdkRemoteAdapter` translate SDK data only at the edge.
The existing `ToolManifest`, `ToolResult`, `RemoteAgentDescriptor`, and
`RemoteAgentResult` remain public domain contracts. Compatibility is explicit:

```text
protocol revision -> SDK version -> enabled extensions -> auth mode -> status
MCP 2026-07-28 -> mcp 2.2.0 -> tools/discovery -> configured bearer/OAuth -> supported
MCP 2025-11-25 -> mcp 2.2.0 -> negotiated fixture tools -> no auth fixture -> supported-legacy
A2A 1.0 -> a2a-sdk 1.1.2 -> card/task/artifact/cancel -> configured auth -> supported
A2A 0.3 -> a2a-sdk 1.1.2 compat -> explicit migration adapter -> fixture only -> deprecated
```

The gateway never accepts SDK objects as canonical values. It validates owned
schemas, applies tool scopes/timeouts, and records compatibility provenance.
Malformed or unsupported protocol data fails with a versioned, actionable
error; it is never silently reinterpreted.

All research-originated payloads enter as `untrusted_external`. Source text,
tool output, remote agent artifacts, browser DOM, feed entries, repositories,
and comments are data—not instructions. The policy layer denies external input
the ability to add scopes, change policy, choose a model/provider, invoke a
write tool, alter a delegated authority grant, or write durable memory directly.

Every tool and agent invocation receives a minimum-authority context containing:

```text
trust_level, source_identity, effect_classification, delegated_authority,
tool_scope, network_scope, memory_write_authority, trace/provenance identifiers
```

Only the Lead Agent may *propose* a durable research-derived memory write. The
runtime validates program scope, evidence linkage, source trust, policy, and
`memory_write_authority` before the repository persists it. External content
cannot self-certify as verified or trusted.

### 2. Model execution

`StructuredModelGateway` wraps the existing `ModelGateway`. It selects a
configured runtime by capability/policy, submits a JSON-schema response request,
validates the returned object before it reaches an agent, and records one
`model_invocation` per call. Static and mock HTTP providers remain deterministic
CI defaults; a configured OpenAI-compatible endpoint is the production path.

The invocation record captures provider, model, provider model version when
returned, input/output hashes, input/output artifact references, latency, token
and usage values, estimated/actual cost hooks, failure status, parent agent run,
job, trace, and span. Schema-invalid results raise `ModelOutputInvalidError`,
record a failed invocation, and cannot become strategy, topic, package, claim,
memory, or brief state.

Lead, Research, and Strategy runtimes consume the same gateway through model
policy selection; model IDs are not embedded in their manifests.

### 3. Research sources and browser access

`ResearchSourceConnector` is an owned read-only contract. Every fetch returns
a `FetchedSource` with source ID/version/type, query/cursor/window, canonical
resource ID and URL, published/fetched timestamps, raw identity/content hash,
rate-limit metadata, trust metadata, provenance, and a bounded raw payload.

Initial connectors are deliberately diverse and narrow:

1. `RssAtomConnector` fetches operator-configured publisher feeds over direct
   HTTP, respecting allowlisted domains, conditional fetch metadata, timeout,
   byte limit, and feed-provided publication timestamps.
2. `HackerNewsConnector` consumes the official versioned Firebase API for
   technology/community signals; source identity is the HN item ID and available
   score/comment count are measurable engagement fields.
3. `BrowserResearchTool` is an optional escalation for an allowed URL where a
   feed/API cannot provide readable public content. It uses one new Playwright
   browser context per run, read-only navigation, allowlisted domains, fixed
   timeout/step/response-byte limits, download denial, and owned trace,
   screenshot, and text-extraction artifacts.

Connector configuration contains only endpoints, public source settings,
allowlists, limits, and optional secret-reference IDs. It never embeds a secret
value. A configured connector cannot broaden its own network scope. CAPTCHA,
paywall, robots/access-control, unsupported auth, and blocked navigation are
reported as bounded source failures rather than bypassed.

### 4. Canonical intelligence records

Migration `0004_intelligence_loop` extends the existing foundation rather than
creating a second content/research store:

| Entity | Purpose and critical links |
| --- | --- |
| `research_sources` | Versioned source configuration, connector version, trust, network/rate limits, and protocol metadata. |
| `research_fetches` | Idempotent fetch attempt keyed by source/resource/window; records request fingerprint, cursor, timing, status, provenance, and artifacts. |
| `research_evidence` (extended) | Existing evidence record gains canonical source/fetch IDs, publisher/author, published time, content hash, rights/use notes, excerpt, source trust, and agent/tool lineage. |
| `signals` | Provider-neutral observation with explicit nullable/available features: freshness, velocity, engagement, source diversity, niche/audience relevance, saturation, novelty, confidence, risk, and source trust. |
| `signal_support` | Preserves every evidence/fetch/source mapping for a merged signal. |
| `topic_opportunities` | Durable candidate/rejected opportunity: topic, why-now, audience, supporting signals, feature availability, deterministic score, bounded semantic judgement, explanation, risks, and status. |
| `content_queue_entries` | Prioritized topic queue/calendar with a source opportunity and strategy version; no publishing state. |
| `strategic_packages` | Immutable package candidate linked to one opportunity: audience, problem/desire, angle, hook, promise, format, duration, opening concept, novelty, evidence requirements, risk notes, and diversity fingerprint. |
| `package_evaluations` | Deterministic constraints, bounded semantic judgement, selection/rejection reason, and evaluator lineage for every package. |
| `claims` and `claim_evidence` | Claim status/confidence/freshness and explicit supporting/contradicting evidence links. |
| `content_brief_versions` | Immutable selected brief linked to opportunity, package, strategy, claims, evidence, prohibitions, source references, risks, and recommended next action. |
| `model_invocations` | Structured model-runtime accounting and lineage described above. |

All rows carry workspace/content-program scope, tenant hook, timestamps,
trace/span, and provenance references where applicable. Foreign keys protect
the lineage `brief -> package -> opportunity -> signals -> evidence/fetch/source
-> tool/model/agent run -> Lead run -> content program`. Idempotency constraints
prevent duplicate source fetches, evidence writes, signal support, packages,
and brief versions after a retry.

`memory_records` is extended with the required research-derived metadata:
`verified_at`, `valid_until`, `source_identity`, `effect_classification`,
`tool_scope`, `network_scope`, `memory_write_authority`, and a structured
`writer_actor`. Existing `trust_level`, `confidence`, `superseded_by`,
`conflict_set`, `evidence_ids`, and provenance remain authoritative.

### 5. Phase 5 durable run

`IntelligenceLoopWorkflow` is a Temporal workflow, started through the existing
`WorkflowBackend` and represented by a normal canonical `jobs` row. It has
checkpoints after source selection, each idempotent fetch, normalization/dedup,
opportunity ranking, strategy proposal, and queue update. All network/model
work executes in retry-bounded activities; activities write canonical records
through idempotent repositories before returning IDs to the workflow.

The Lead Agent owns the run and calls Research and Strategy through the existing
native `AgentService` interface. Direct and delegated calls accept the same
typed request and produce the same result/artifact contracts. Child agent runs,
tool/model invocations, and browser artifacts preserve the parent run and trace
lineage. Cancellation stops new child/tool work and persists a cancellable
checkpoint; it does not erase already captured evidence.

Signal normalization retains missing fields as absent plus an availability map.
Deduplication uses a stable canonical resource identity where possible; otherwise
a normalized title/topic fingerprint. A merge preserves all `signal_support`
rows. Ranking uses a transparent clamped score:

```text
100 * clamp(
  .18*freshness + .14*velocity + .14*source_diversity + .20*niche_relevance
  + .14*audience_relevance + .12*novelty + .08*confidence
  - .12*saturation - .12*risk,
  0, 1
)
```

Values are only included when supplied or deterministically derived from source
metadata; missing fields stay missing. A schema-valid model may add a bounded
semantic adjustment in `[-10, 10]` with an explanation, never replace the
deterministic score, and never remove the raw feature evidence. The Strategy
Agent proposes an immutable `StrategyVersion`; the Lead Agent explicitly marks
it accepted or leaves it provisional. No strategy is overwritten.

### 6. Phase 6 package-to-brief run

For a selected opportunity, the Lead/Strategy path creates 3–5 materially
different `StrategicPackage` candidates. Each is validated against the owned
schema and gets a diversity fingerprint from normalized audience, angle, hook,
promise, and format terms. Candidates with high normalized-token overlap and no
material difference are rejected with a stored reason rather than counted as
variants.

Every package receives deterministic checks for required fields, audience and
strategy match, evidence availability, saturation, risk, and misleading/clickbait
markers. A schema-valid model supplies a bounded semantic evaluation for
clarity, novelty, curiosity, promise strength, credibility, and brand fit. The
selected candidate stores its deterministic and semantic scores, evaluator run,
and selection rationale. All alternatives remain available for later analysis.

The Evidence Workspace then creates/reuses source-linked evidence and explicit
claims. A claim cannot become `verified` without at least one eligible support
record; contradictory evidence prevents automatic verification and is surfaced
in the brief. `ContentBriefVersion` is immutable, never script text, and holds
topic/opportunity, chosen package, audience/intent/promise/format/creative
direction, required claims/evidence, prohibited claims, source/strategy refs,
known risks/questions, and recommended Phase-7 action.

## Public Contract and Scheduling

New public input types are versioned Pydantic/JSON-schema DTOs, never provider
SDK types:

```text
ResearchRequest@v1(niche, topic?, source_ids?, cursor/window?, constraints?)
SignalResearchRequest@v1(program_id, niche, source_ids?, schedule_id?)
PackageRequest@v1(program_id, opportunity_id, candidate_count=3..5)
ContentBriefRequest@v1(program_id, opportunity_id, selected_package_id)
IntelligenceRunRequest@v1(program_id, niche, source_ids?, dry_run, idempotency_key)
```

`research_agent`, `strategy_agent`, `lead_content_agent`, and the new
read-only `browser_research_agent` remain discoverable/directly callable.
Schedules create an ordinary `intelligence_research` job payload and invoke the
same workflow/API; there is no parallel hidden scheduler. Default cadence is
operator-configured and disabled until a schedule is explicitly created.

## Security and Failure Rules

- Default capability is `read_only`; browser/download/write effects require
  separate policy scopes and are out of scope for this build.
- Network requests validate scheme, host allowlist, redirect target, response
  type/size, and timeout. Browser route interception enforces the same policy.
- Each source fetch/model/tool/browser invocation reserves/checks its budget and
  records actual usage when supplied. Budget or permission denial occurs before
  the provider call.
- Retry uses the canonical idempotency key and reconciles existing fetch/evidence
  state before creating another record. Timeouts/unavailable providers are
  durable failed/retryable events with no fabricated signal.
- Prompt-injection fixtures must appear in stored untrusted evidence but cannot
  alter policy/scopes, create trusted memory, or cause an effect.
- Artifact hashes and source/fetch/model/tool/agent lineage are retained. Secret
  values, cookies, authorization headers, and browser storage never appear in
  logs, evidence, or trace artifacts.

## Verification Strategy

Deterministic CI uses local HTTP model, RSS, HN, MCP, A2A, and browser fixtures;
real-network/model smoke tests are separate `-m live` opt-in tests. The new eval
fixtures assert research grounding, normalization, identity/semantic dedup,
ranking, package diversity, package selection consistency, evidence/claim
linkage, schema-invalid model rejection, program memory isolation, and prompt
injection containment.

Integration/e2e tests prove:

1. MCP SDK `2026-07-28` discovery/call and explicit legacy negotiation;
2. A2A 1.0 discovery/task/artifact/cancel plus deliberate 0.3 compatibility or
   clear migration failure;
3. direct and delegated Research/Strategy calls produce the same canonical DTOs;
4. a durable Lead intelligence run survives worker interruption during research
   and resumes without duplicate fetch/evidence/signal rows;
5. the full fixture chain produces a selected immutable ContentBrief with every
   ancestry link; and
6. invalid model output, provider/source timeout, duplicate signals,
   contradictory evidence, budget/permission denial, injection fixture,
   malformed browser result, and cancellation remain safe and inspectable.

Before storage-heavy browser/Compose checks, inspect filesystem and Docker
usage. This environment currently has insufficient free disk for downloading
Playwright browsers or creating broad Temporal stacks. Do not delete project
volumes or perform global cleanup without explicit operator authorization.

## Documentation and Operations

The implementation adds ADRs for each adopted SDK/browser decision, a protocol
compatibility matrix, research/browser/trust/model runbooks, schema lifecycle
documentation, exact deterministic and live-smoke commands, and a Phase-7
handoff. Documentation describes only delivered behavior and calls out optional
credentials/browser binaries, source scope, rate limits, and deferred scripting
or publishing.

## Acceptance Mapping

| Required outcome | Evidence |
| --- | --- |
| Current MCP/A2A boundary | Official-SDK contract tests plus documented matrix/ADR |
| Untrusted research containment | Trust-policy unit/eval fixture and repository persistence assertions |
| Usable real model path | OpenAI-compatible structured gateway fixture and env-gated live smoke |
| API/feed/browser research | Connector contract tests; Playwright fixture test when browser runtime is installed |
| Explainable Phase 5 | Source/fetch/evidence/signal/opportunity/strategy/queue integration test |
| Explainable Phase 6 | Package/evaluation/claim/brief integration test with full lineage query |
| Durable retry/cancellation | Temporal restart/cancel e2e asserting idempotent canonical rows |
| Existing foundation preserved | Existing Phase 1–4 verifier suite remains green |

## Phase-7 Handoff

The only Phase-7 input is a selected immutable `ContentBriefVersion` plus its
claims/evidence/provenance and known risks. A future script/copy capability must
consume this record through a versioned contract; it must not reconstruct
research decisions, mutate evidence, or bypass the claim-verification state.
