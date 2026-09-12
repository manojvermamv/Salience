# Reuse, Extension and Future-Replaceable Design

This is a binding engineering policy. The goal is to use mature real-world software without allowing any framework, provider or vendor to become the product's architecture.

## Default engineering rule

Search before building.

```text
proven free/open/self-hosted component behind our adapter
        -> upstream plugin/extension
        -> proven library inside a bounded service
        -> small maintained patch/light fork
        -> custom implementation when ownership wins
```

"Free" is not enough. Evaluate license, security, maintenance, data ownership, resource cost, operational burden and exit path.

Custom effort should focus on differentiation: agent/domain model, signal intelligence, strategy/packaging, evidence/provenance, autonomous orchestration policy, agent/tool contracts, experiments/learning and the way the system connects everything.

## Framework-neutral multi-agent core

Do not make CrewAI, OpenAI Agents SDK, LangGraph or any current framework the canonical `Agent` type.

Core-owned extension points should include:

```text
AgentRunner
AgentRegistry
AgentRuntimeAdapter
RemoteAgentAdapter
Team/CrewRunner
ModelGateway / ModelProvider
ResearchConnector / SignalSource
SearchProvider
BrowserWorker / AppWorker
CodingAgentWorker
CreativeTool / MediaWorker
Publisher
AnalyticsConnector
ObjectStore
WorkflowBackend / QueueBackend
Notification / ApprovalProvider
```

An agent framework adapter translates canonical `AgentManifest` + run request into that framework and maps results/events/artifacts back.

CrewAI is especially useful as a reference for role-based Crews plus deterministic Flows, and can be supported through an adapter if it wins the build-vs-reuse decision. The desired simple user experience should not be confused with CrewAI API compatibility.

## Callable-agent extension contract

Every agent plugin/runtime must preserve:

```text
agent identity/version
skills/capabilities
typed input/output
tool/memory/secret scopes
effect classification
model policy
budgets/timeouts
run identity/status/events
artifacts
parent/child lineage
provenance
```

A new framework should normally require one adapter plus contract tests, not edits across research, content, publishing and analytics.

## MCP and A2A boundary

Prefer open standards where they fit:

- **MCP**: tools, resources and prompts exposed to models/agents.
- **A2A**: independent/opaque agent systems communicating and collaborating across frameworks/vendors.
- **HTTP/OpenAPI/webhooks**: normal service integration.
- **Native internal contract**: local agents and tools where extra protocol overhead adds no value.

It is acceptable to project a bounded specialist as an MCP tool, but full remote agent lifecycle/discovery should prefer A2A when supported.

Pin and test protocol versions. A2A v1.0 is a stable reference as of this research snapshot; future versions must be adopted through compatibility tests/migrations.

## Plugin manifests

Every replaceable component should publish:

```text
plugin_id
plugin_version
contract_version
capabilities
configuration_schema
required_secret_scopes
effect classification
health/status
limits/timeouts
optional cost/usage metadata
transport/protocol metadata
```

Agent manifests add agent skills, input/output modes and model/memory policies.

## Reuse candidates by layer

Usually reuse mature infrastructure for:

- PostgreSQL, object storage, cache;
- Temporal/other durable workflow engines;
- queues/schedulers;
- OAuth/auth/secret libraries;
- model gateways/provider adapters;
- agent frameworks behind adapters;
- MCP/A2A SDKs;
- Playwright/browser/Appium tooling;
- FFmpeg/media primitives;
- creative-provider APIs/SDKs;
- social publishing platforms/APIs;
- metrics/logs/traces/dashboards;
- migrations/schema validation/test frameworks.

Do not build a custom workflow engine, browser engine, OAuth stack, FFmpeg replacement or social API client ecosystem unless the build-vs-reuse analysis clearly justifies it.

## Data and anti-lock-in rules

- third-party IDs are mappings to our IDs;
- third-party databases are never the only store for core domain history;
- prompts/policies/agent manifests are versioned in our system;
- generated artifacts go to portable owned storage where allowed;
- agent/tool/model provenance stays in our system;
- export core data without requiring a vendor SaaS account;
- one integration outage should not corrupt unrelated capabilities.

## Creative-tool openness

Treat creative providers as a capability market. Higgsfield, HeyGen, Synthesia, Veo, open/local models and future services can coexist.

Transport may be API, SDK, CLI, MCP, webhook, local process or browser/app flow; the canonical creative job/artifact contract does not change.

## Durable-workflow openness

A durable runtime is mandatory; a specific engine is not. Strongly evaluate Temporal because it already solves long-running state, retries, timers and recovery. CrewAI Flows may be useful for smaller/hybrid agent workflows. n8n may be an integration/operator layer. Keep core state and domain policy outside these products.

## Compatibility and upgrades

Use:

- semantic/versioned contracts;
- dependency injection/capability discovery;
- shared contract tests;
- feature flags/canary rollout;
- backward-compatible readers where practical;
- explicit data/config migrations;
- deprecation windows;
- provider/agent fallback;
- compatibility matrix.

No architecture can guarantee zero migration forever. The goal is to **localize change**.

## Fork policy

Fork only when configuration, APIs, plugins or wrappers cannot satisfy the need.

If a fork is necessary:

- pin upstream release/commit;
- keep patches minimal;
- document local changes;
- preserve upstream tests;
- automate rebase/comparison checks;
- retain a path back upstream or to another implementation.

## Total-cost decision

Evaluate:

`license + compute + memory + storage + network + maintenance + upgrades + security + failure recovery + operator complexity`

The best component is the lowest-lifecycle-cost option that satisfies reliability and replaceability—not the one with the most features or stars.
