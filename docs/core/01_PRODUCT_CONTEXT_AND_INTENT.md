# Product Context and Intent

## Product goal

This is a self-hosted **multi-agent autonomous content operating system**, not a simple AI post generator. Its long-term interaction should be close to:

```text
User:
  Niche: Personal Finance
  optional constraints: brand, audience, channels, budget, language, risk limits

System:
  understand niche
  -> research market/audiences/competitors/trends
  -> build strategy and content calendar
  -> discover topics
  -> create and test strategic packaging
  -> research each item and build evidence
  -> write scripts/copy
  -> create image/video/audio
  -> edit and repurpose
  -> create distribution packaging
  -> verify/govern
  -> publish/distribute
  -> collect analytics
  -> run measured experiments
  -> learn
  -> repeat
```

The user should not have to configure every internal agent. The system can derive provisional audience, strategy and operating defaults from the niche, then ask only when a missing choice is genuinely blocking or policy requires human authorization.

"100M Content System" is a product ambition and design target for scale/quality, **not a guarantee of views**.

## Autonomy model

One durable **Lead Content Agent / Supervisor** is accountable for the complete cognitive loop. It understands the current niche and strategy, decides what work is needed, selects models/tools/agents, judges results, replans and closes the feedback loop.

Under it is a library of **first-class specialist agents**. These are reusable logical agents such as research, strategy, creative direction, production, verification or growth. They can be called by the Lead Agent, by another authorized agent, or directly by an end user. They do not need separate servers or separate models; one model can back many logical agents.

Do not turn every function into an agent. Deterministic utilities, search connectors, renderers, validators, publishers and APIs should stay tools when agent reasoning adds no value. The design is multi-agent, not "agentize everything."

## Core principles

1. **Lead Agent owns semantic orchestration.** It decides the next meaningful content action and when to delegate or re-plan.
2. **Specialist agents are callable products.** Every enabled specialist has a stable manifest, typed input/output, version, permissions, budgets and a simple invocation surface.
3. **Agent identity is not model identity.** The same model may power many agents; one logical agent may change models without losing identity or history.
4. **Runtime owns hard guarantees.** Durable state, queues, retries, idempotency, credentials, budgets, policy, audit and persistence are deterministic infrastructure.
5. **Roles are not automatically agents.** "Writer", "fact checker", "SEO", "browser researcher" or "video producer" may be a mode, tool, evaluator or agent depending on the actual need.
6. **Model/framework/tool agnostic.** CrewAI, OpenAI Agents SDK, LangGraph, custom runners and future frameworks are implementation adapters, not the domain model.
7. **MCP and A2A have different jobs.** MCP is primarily for tools/resources/context; A2A is the preferred interoperability path for independent remote agents. Internal agents use our native contract.
8. **API-first, browser/app capable.** Prefer stable APIs. Use browser/app control when no better integration exists.
9. **One source of truth.** Core content, agent/run, evidence, artifact, publication, metric and experiment identities live in our system.
10. **Evidence before confident factual claims.** Research provenance and verification state must survive into downstream content lineage.
11. **Strategic packaging before scripting.** Audience, angle, hook, promise and format are decided/tested before full writing.
12. **Distribution packaging later.** Thumbnail/title/caption/SEO/metadata/localization are optimized for the final asset and target platform.
13. **Verification is cross-cutting.** Facts, copyright/rights, brand, safety, platform policy, duplicates and technical validation happen where they matter, plus a final gate.
14. **Learning is measured and reversible.** Analytics can change behavior only through versioned experiments with keep/rollback decisions.
15. **Reuse before rebuild.** Mature free/open/self-hosted components are evaluated before custom commodity infrastructure.
16. **Open-door extension design.** Every major model, agent, tool, workflow backend, publisher or creative system is replaceable through a versioned contract.
17. **Core data outlives integrations.** Replacing a provider cannot erase historical identity, evidence, metrics, audit or workflow state.
18. **Human authority remains available.** High-risk or irreversible effects can require approval even in autonomous mode.

## Specialist-agent model

A specialist agent should be definable independently from its runtime implementation. A minimum logical manifest includes:

```text
agent_id
agent_version
name / description
skills / capabilities
input_schema
output_schema
default tool scopes
memory/context scopes
model policy
effect classification
budget/time limits
sync/async support
health/status
```

The same specialist should be invokable from:

```text
Lead Agent / another agent
REST/API
CLI
Python/TypeScript SDK
optional MCP projection
optional A2A server/card for remote interoperability
```

A team/crew is a reusable composition of agents, but the underlying agents remain individually callable.

## Context and memory

Do not dump all system memory into every agent. Separate:

- working/session context for the current run;
- durable niche/brand/audience knowledge;
- evidence/source knowledge;
- episodic content and decision history;
- analytics/experiment history;
- artifacts and structured state.

Each agent gets the minimum context and secret/tool scope required for its task. The Lead Agent can retrieve more when needed.

## Creative direction

Creative production is provider-neutral and agent-driven. The Lead Agent or a Creative/Production specialist can call multiple providers, create variants, inspect/compare outputs and iterate. Higgsfield, HeyGen, Synthesia, Google Veo, local/open models, FFmpeg-based pipelines and future systems are adapters, not permanent architecture.

## Operating modes

- `research_only`: no content leaves the system.
- `draft`: full content can be created but not published.
- `approval`: prepare publication and wait for required approval.
- `sandbox_publish`: publish only to test/sandbox destinations.
- `autonomous`: allow configured low-risk/live actions after policy checks.

Development starts in `draft` or `sandbox_publish`.

## Non-goals

- Do not make one unrestricted agent process hold every credential.
- Do not create a permanent agent just because a stage has a name.
- Do not hard-code CrewAI, one model vendor, one creative vendor, one scheduler or one browser engine into core entities.
- Do not assume "autonomous" means bypassing approvals, terms, platform controls or security.
- Do not treat GitHub stars, marketing demos or a "100M views" label as proof of expected performance.
- Do not copy competitors' content; learn from public signals/structures and generate original work with provenance and rights awareness.
