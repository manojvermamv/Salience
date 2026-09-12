# Target Architecture

## Main architectural model

```text
                         +---------------------------+
                         |           HUMAN           |
                         |                           |
                         |      Pick the NICHE       |
                         |   + optional constraints  |
                         +-------------+-------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                    AUTONOMOUS CONTENT CONTROL PLANE                         |
|                                                                             |
| Goals / brand policy / audiences / channels / budgets / permissions         |
| schedules / success metrics / risk limits / platform rules / feature flags |
+-------------------------------+---------------------------------------------+
                                |
                                v
                    +----------------------------+
                    |  MAIN AI AGENT / SUPERVISOR|
                    |                            |
                    | understand niche           |
                    | bootstrap strategy         |
                    | plan and delegate          |
                    | choose models/tools/agents |
                    | judge results              |
                    | re-plan / learn            |
                    +-------------+--------------+
                                  |
                   +--------------+----------------+
                   |                               |
                   v                               v
       +-------------------------+      +--------------------------+
       | DURABLE WORKFLOW RUNTIME|<---->| CONTEXT + MEMORY SYSTEM  |
       |                         |      |                          |
       | state / checkpoints     |      | niche / brand knowledge |
       | jobs / schedules        |      | audience / strategy     |
       | events / queues         |      | evidence / sources      |
       | retries / recovery      |      | past content / artifacts|
       | timeouts / idempotency  |      | analytics / experiments |
       +-------------+-----------+      +--------------------------+
                     |
                     v
+-----------------------------------------------------------------------------+
|                  CALLABLE SPECIALIST AGENT REGISTRY                         |
|                                                                             |
| Research / Strategy / Creative / Production / Verification / Growth / ...   |
|                                                                             |
| Each is a first-class callable logical agent with typed contracts.          |
| Not every role requires a different model, process or deployment.           |
+-----------------------------------+-----------------------------------------+
                                    |
              +---------------------+-----------------------+
              |                     |                       |
              v                     v                       v
+----------------------+ +-----------------------+ +---------------------------+
| MODEL GATEWAY        | | TOOL / MCP GATEWAY    | | EXTERNAL AGENT GATEWAY    |
|                      | |                       | |                           |
| LLM / reasoning      | | web / browser         | | A2A remote agents         |
| vision / image       | | search / trends       | | vendor agents             |
| video / speech/music | | SEO / docs / code     | | external specialist svcs  |
| multiple vendors     | | CMS / APIs / media    | |                           |
+----------------------+ +-----------------------+ +---------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------------+
|                         CONTENT FACTORY                                     |
|                                                                             |
| Discover -> Research -> Strategy -> STRATEGIC PACKAGING -> Brief/Evidence   |
| -> Script/Copy -> Images/Video/Audio -> Edit/Compose -> Repurpose            |
| -> DISTRIBUTION PACKAGING -> Localization                                   |
|                                                                             |
| Strategic packaging: audience / angle / hook / promise / format             |
| Distribution packaging: title / thumbnail / caption / SEO / metadata        |
+-----------------------------------+-----------------------------------------+
                                    |
                        cross-cutting verifiers/evals
                                    |
                                    v
+-----------------------------------------------------------------------------+
|                       VERIFY / EVALUATE / GOVERN                            |
|                                                                             |
| facts / sources / brand / quality / rights / copyright / safety             |
| platform policy / duplicate detection / hallucination / technical checks    |
| final pre-publish gate; human approval only when policy/risk requires it     |
+-----------------------------------+-----------------------------------------+
                                    |
                                    v
                     +------------------------------+
                     | PUBLISH & DISTRIBUTE         |
                     | platforms / website / email  |
                     | scheduler / CMS / APIs       |
                     +--------------+---------------+
                                    |
                                    v
                     +------------------------------+
                     | ANALYTICS + EXPERIMENTS      |
                     | views / retention / CTR      |
                     | engagement / conversion      |
                     | revenue / A-B tests / signals|
                     +--------------+---------------+
                                    |
                                    v
                     +------------------------------+
                     | LEARNING / OPTIMIZATION      |
                     | interpret -> propose -> test |
                     | keep / rollback -> memory    |
                     +--------------+---------------+
                                    |
                                    +-----> MAIN AI AGENT
```

The stage arrows are a **capability map**, not a rigid semantic state machine. The Lead Agent may revisit research, create more packaging candidates, call multiple specialists in parallel, skip irrelevant stages or re-run creative production when evidence/analytics justify it.

## Deterministic substrate under the AI layer

```text
                       AI / Agent Intelligence
                                ^
                                |
---------------------------------------------------------------------
 Durable Workflows | State | Events | Permissions | Agent Registry
 Artifact Storage  | Queue | Cache  | Scheduling  | Checkpoints
 Model Gateway     | MCP   | A2A    | Sandboxes   | Browser/App
 Observability     | Evals | Cost   | Provenance  | Reconciliation
 Secrets           | Auth  | Policy | Audit       | Backups
---------------------------------------------------------------------
                         Infrastructure
```

The substrate makes agents durable and safe. It does **not** decide creative meaning by default.

## Callable specialist-agent architecture

Every enabled specialist is a first-class logical agent with a canonical `AgentManifest`. The internal registry owns identity; a framework adapter can realize that agent using CrewAI, OpenAI Agents SDK, another framework or our own runner.

Minimum manifest:

```text
agent_id
agent_version
display_name
description
skills / capability tags
input_schema / output_schema
default_tools
memory_scopes
required_secret_scopes
model_policy
effect_classification
default_budget / timeout
sync_async_modes
health/status
```

Recommended native surfaces:

```text
GET  /v1/agents
GET  /v1/agents/{agent_id}
POST /v1/agents/{agent_id}/runs
GET  /v1/agent-runs/{run_id}
POST /v1/agent-runs/{run_id}/cancel
GET  /v1/agent-runs/{run_id}/events
```

Equivalent ergonomic clients should exist:

```text
CLI:
  content agents list
  content agent run research --input request.json

Python:
  client.agents.run("research", input={...})

TypeScript:
  client.agents.run("research", { input: ... })
```

A long-running invocation returns a `run_id`; artifacts/events can stream or be polled. The same specialist contract is used when the Lead Agent invokes it internally.

Support reusable `TeamManifest` / "crew" compositions as convenience, while preserving direct agent invocation.

## Internal agents, MCP and A2A

Use the right integration level:

- **Native Agent Contract**: default for local/in-process/containerized specialists.
- **MCP**: expose or consume tools/resources/prompts. A specialist may be projected as an MCP tool for simple bounded invocation.
- **A2A v1.x**: preferred protocol for independent remote/opaque agent systems that need discovery, skills, stateful tasks, streaming or asynchronous collaboration.
- **Framework adapters**: CrewAI/OpenAI Agents/LangGraph/etc. translate our canonical agent contract into their native runtime model.

Do not make MCP the agent-to-agent protocol merely because it is available. Do not require A2A for simple local agents.

## Supervisor and delegation

The `LeadContentAgent` uses a manager/supervisor pattern by default:

```text
Lead Agent
  -> inspect goal/context
  -> choose tool or specialist
  -> invoke one or several agents
  -> receive structured outputs/artifacts
  -> evaluate
  -> re-plan
  -> continue
```

Support explicit handoff mode when a specialist should temporarily own a conversation/task, but normal content jobs retain one accountable parent/supervisor.

A specialist is not necessarily a separate model. A child run can use the same provider/model with a different prompt, context and tool scope.

## Control plane and durable workflow runtime

The control plane stores operating policy and exposes administrative control. The durable workflow runtime owns:

- run/task identities and checkpoints;
- retries, backoff, timeouts and dead-letter handling;
- schedules, timers and dependency waits;
- idempotency/reconciliation for external effects;
- per-run/agent/provider budgets;
- permissions and approval requirements;
- schema and contract validation;
- provenance/audit events;
- replay/resume/cancel;
- long-running async jobs and human waits.

Use a mature durable-execution system when it fits. Temporal is a strong candidate because long-running agent work is a distributed-systems problem; CrewAI Flows or other systems may fit smaller deployments. Keep a `WorkflowBackend` boundary so the product is not permanently coupled to one engine.

## Context and memory system

Use PostgreSQL as the source of truth plus S3-compatible object storage for artifacts. Optional vector/search indexes improve retrieval but do not replace canonical records.

Memory is scoped:

```text
working memory       current run/agent scratch context
semantic memory      niche / brand / audience / strategy knowledge
evidence memory      sources / claims / provenance
episodic memory      past content decisions and outcomes
analytics memory     metrics / experiments / learned hypotheses
artifact memory      scripts / media / publication assets
```

Agents request/retrieve only the context they need. Do not broadcast all memory to every specialist.

Core entities should include:

- `agent_definition`, `agent_version`, `agent_run`, `agent_delegation`;
- `team_definition` / `team_version`;
- `tool_definition`, `tool_run`;
- `source`, `signal`, `topic`, `topic_score`;
- `strategy_version`, `content_calendar`;
- `package_candidate`, `content_brief`;
- `evidence_item`, `claim`;
- `script_version`, `asset`;
- `publication`, `metric_snapshot`;
- `experiment`, `prompt_version`, `policy_version`;
- `model_run`, `approval`, `audit_event`.

Every published item must trace to the agent runs, tools/models, topic/package/script/assets and evidence that produced it.

## Model gateway

Agents ask for model capabilities and quality/cost constraints, not hard-coded brands. The gateway can route one logical agent to different models and can allow a specialist to pin a provider when a unique capability is required.

Support provider APIs, OpenAI-compatible endpoints and custom static gateways. LiteLLM is a candidate implementation behind our own `ModelGateway` contract.

## Tool and creative gateways

Tools are capabilities, not agents unless reasoning/autonomy is required. The registry may contain:

- research/search/browser/app tools;
- code/sandbox/document tools;
- creative media tools;
- publishers/CMS/social APIs;
- analytics connectors;
- notification/approval tools.

Creative provider adapters should support long-running job lifecycles, variants, refinement, cancellation, cost/rate limits, provider-specific extensions and artifact reconciliation into owned storage. Higgsfield, HeyGen, Synthesia, Veo and MoneyPrinterTurbo are examples, not fixed dependencies.

## Content-factory correction: two kinds of packaging

**Strategic packaging comes before full scripting**:

```text
audience -> problem/desire -> angle -> hook -> promise/payoff -> format -> opening visual
```

**Distribution packaging comes after/alongside production**:

```text
title -> thumbnail -> caption -> description -> SEO/keywords -> platform metadata
-> localization / channel-specific variants
```

Do not combine these into one late "packaging" step.

## Verification architecture

Verification is both cross-cutting and final.

Examples:

- research/evidence: source identity, freshness, claim support;
- strategy/packaging: originality, audience fit, promise-content consistency;
- script: factual claims, citations, brand/style rules;
- media: technical validation, rights/provenance, visual/audio quality;
- distribution packaging: policy, misleading-claim checks, platform constraints;
- publishing: idempotency, account/channel permission, final approval gate;
- learning: statistical/operational guardrails and rollback criteria.

A specialist verifier agent may perform semantic evaluation, but deterministic validators should enforce rules that can be encoded exactly.

## Publishing, analytics and learning

Publishing adapters derive platform payloads from one canonical publication identity. Prefer official APIs/self-hosted publishing services; browser posting is a fallback.

Collect metric snapshots across multiple time horizons and attribute them to topic, source signals, strategic package, script, assets, distribution package, timing, platform and behavior/agent versions.

Learning produces proposals and experiments, not silent self-rewrites. Every behavior change has a hypothesis, treatment version, control/baseline, metric/guardrails, minimum sample/time and keep/rollback result.

## Deployment

Start with Docker Compose on one Linux server:

```text
control/API
agent runtime workers
durable workflow backend
PostgreSQL
object storage
model gateway
browser/app sandbox workers
optional creative/publishing services
observability
```

Scale workers horizontally later without changing agent/tool/domain contracts. Optional integrations must be independently installable/removable.
