# Callable Agents and Engineering Method

## Why this file exists

The product is a multi-agent autonomous system, but it must remain simple for both the Lead Agent and end users. Specialist agents therefore behave like reusable callable capabilities rather than hidden prompt fragments.

This file also defines the engineering method the system-building AI agent should use.

## Canonical callable-agent contract

A specialist agent is a versioned logical unit independent of its model/framework.

Example manifest:

```yaml
agent_id: research_agent
version: 1.0.0
name: Research Agent
description: Investigates markets, sources, competitors and evidence.
skills:
  - trend_research
  - competitor_research
  - source_verification
input_schema: ResearchRequest@v1
output_schema: ResearchResult@v1
tool_scopes:
  - web.search
  - browser.read
memory_scopes:
  - niche
  - evidence
model_policy: balanced_reasoning
effect_class: read_only
default_timeout: 20m
default_budget: ...
modes:
  - sync
  - async
```

Invocation:

```http
POST /v1/agents/research_agent/runs
Content-Type: application/json

{
  "input": {...},
  "context_refs": [...],
  "constraints": {...},
  "mode": "async"
}
```

Response:

```json
{
  "run_id": "...",
  "status": "queued",
  "agent_id": "research_agent",
  "agent_version": "1.0.0"
}
```

The result is structured and can reference artifacts/evidence rather than embedding huge payloads.

## User-facing ergonomics

Offer thin clients:

```bash
content agents list
content agent describe research_agent
content agent run research_agent --input request.json
content agent status <run_id>
content agent cancel <run_id>
```

```python
result = client.agents.run(
    "research_agent",
    input={"niche": "personal finance", "goal": "find emerging topics"}
)
```

A team/crew is similarly callable:

```python
result = client.teams.run("short_video_factory", input={...})
```

but each member remains independently invokable.

## How frameworks fit

The canonical interface is ours. Implementations may use:

- CrewAI Crew/Agent/Flow;
- OpenAI Agents SDK manager/agents-as-tools/handoffs;
- LangGraph or another graph runtime;
- a custom lightweight loop;
- a remote A2A agent.

This means the system can gain the simple callable behavior users like in CrewAI without becoming CrewAI-dependent.

## MCP vs A2A

Use MCP for vertical access to tools/resources/prompts. Use A2A for horizontal collaboration between independent agents.

Practical rule:

```text
Need a database/search/browser/video function?
  -> tool / MCP

Need another autonomous agent/service with its own reasoning/task lifecycle?
  -> native local agent contract or A2A if remote/interoperable
```

A bounded local specialist can still be projected as an MCP tool for convenience.

As of the 2026-09-12 research snapshot, A2A v1.0 is a stable protocol and defines discoverable Agent Cards, skills and long-running tasks. Track future versions through an adapter, not domain changes.

## Supervisor rules

The Lead Agent should not delegate reflexively.

Delegate when one or more are true:

- parallel exploration is valuable;
- independent evaluation reduces correlated mistakes;
- the task needs isolated long context;
- a specialist has unique tools/knowledge;
- a long-running browser/media loop would pollute the Lead Agent context;
- separate permissions/budgets are useful.

Otherwise the Lead Agent can simply do the work itself using another model call/tool.

## Engineering method: Spec, Verifier, Environment

Use a **Karpathy-inspired** three-part discipline:

### Spec

Capture the outcome, intent, architecture constraints, invariants and definition of done. Resolve material ambiguity but do not prescribe implementation details the engineering agent can determine safely.

Specs are **iterative contracts**, not one giant waterfall document.

### Verifier

Define how correctness will be observed before or alongside implementation:

- unit/integration tests;
- types/build/lint;
- contract tests;
- runtime probes;
- architectural invariant checks;
- security checks;
- end-to-end fixtures;
- task-specific evals.

"The code looks correct" is not verification.

### Environment

Use the real environment:

- repository and current architecture;
- source/docs/tests;
- dependencies and APIs;
- Git history;
- logs/traces;
- schemas/migrations;
- deployed/runtime behavior;
- browser/tools/sandboxes;
- project knowledge/decision files.

A giant prompt is not a substitute for inspecting the environment.

## Iterative engineering loop

```text
Understand
-> define/update Spec
-> define/update Verifier
-> inspect Environment
-> Plan coherent slice
-> Implement
-> Verify
-> Inspect failures/evidence
-> Repair/adjust
-> Re-verify
-> Checkpoint
-> Repeat
```

Choose the **largest coherent slice that remains safely understandable, independently verifiable and reversible**.

Avoid:

```text
Waterfall:
  plan whole project -> change whole project -> verify at end

Micromanagement:
  human dictates every file/function/step despite adequate agent capability
```

Target:

```text
Human/project intent decides WHAT and WHY.
Engineering agent normally decides HOW.
Small enough to verify; large enough to be meaningful.
```

If implementation evidence shows the current architecture/spec assumption is wrong, update the plan/spec and proceed from the newly verified understanding instead of blindly following the old plan.

## Attribution correction

The exact phrase **"Spec -> Verifier -> Environment" should not be presented as a verified verbatim three-layer framework published by Andrej Karpathy**. Current secondary material explicitly describes it as a teaching scaffold built from Karpathy's broader ideas around context, verification and persistent agent environments.

Use wording such as:

> "Karpathy-inspired Spec–Verifier–Environment discipline"

rather than:

> "Karpathy's official three-layer framework."

Relevant context/reference material:

- Karpathy LLM Wiki primary idea: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- Secondary explanation that labels Spec/Verifier/Environment as a teaching scaffold: https://guides.kno2gether.com/karpathy-method/
- Secondary three-layer explanation: https://www.vensas.de/en/blog/karpathy-three-layers

The engineering principles themselves remain useful and model-agnostic regardless of attribution.
