# Lead Agent, Specialist Agents and Creative Tool Runtime

This document defines runtime behavior for the multi-agent content system.

## Lead Agent / Supervisor

Maintain one durable logical `LeadContentAgent` for each content program/workspace. It owns:

- understanding the niche and goals;
- choosing what to investigate;
- strategy and priority decisions;
- deciding when to call tools or specialist agents;
- judging and combining specialist results;
- planning/replanning;
- deciding when more research/variants are needed;
- interpreting analytics and experiments;
- deciding the next cycle.

A new model call is not automatically a new agent. Agent identity persists across model/provider changes and process restarts.

## Specialist agent layer

The product is intentionally **multi-agent**, but specialists are first-class callable logical components rather than permanent model deployments.

Initial useful agent families may include:

```text
research_agent
strategy_agent
creative_director_agent
writer_agent
production_agent
verifier_agent
growth_agent
browser_research_agent
```

These are starting points, not a fixed taxonomy. Merge/remove/add agents based on measured utility.

Do not create an agent for deterministic work that a normal tool/validator handles better.

## End-user callable behavior

Every enabled specialist should be as easy to use as a small standalone service:

```text
client.agents.run("research_agent", input={...})
client.agents.run("creative_director_agent", input={...})
```

The Lead Agent invokes the exact same canonical contract.

Support async jobs for heavy tasks and reusable teams/crews for common multi-agent compositions.

See `09_CALLABLE_AGENTS_AND_ENGINEERING_METHOD.md` for the exact contract.

## Manager, handoff and parallel modes

Default: **manager/supervisor mode**. A child agent returns structured output/artifacts and the Lead Agent remains accountable.

Optional modes:

- `handoff`: specialist temporarily owns the task/conversation;
- `parallel`: multiple children explore independent alternatives;
- `review`: independent specialist evaluates an existing artifact;
- `heavy_worker`: specialist owns a long tool loop and returns a compact result.

A specialist may use the same model as the Lead Agent. Separate providers are optional.

## Heavy creative jobs

Example:

```text
Lead Agent
  -> Creative Director
      -> define shot/creative brief
      -> Production Agent
           -> call 2-3 eligible video/image tools
           -> create variants
           -> poll async jobs
           -> reject technical failures
           -> collect previews/metadata
           -> compare candidates
           -> return ranked artifacts
  -> Lead Agent chooses/refines/continues
```

The durable runtime stores each external job and can resume after crashes without repeating paid generation unnecessarily.

## Creative capability contract

Discover providers by capabilities such as:

```text
generate_image
image_edit
text_to_video
image_to_video
reference_guided_video
first_last_frame_video
extend_video
avatar_video
text_to_speech
voice_clone
lipsync
dub_video
translate_video
music_or_sfx
caption
compose_or_edit_video
upscale
creative_analysis
```

Provider manifests should include supported formats/aspect ratios/resolutions/durations, model versions, latency expectations, async/webhook support, concurrency/rate limits, cost/credit hints, credential scopes, content-policy constraints and health.

Requests use canonical intent + constraints. Provider-specific knobs live in adapter extensions.

## Provider direction — research snapshot 2026-09-12

### Higgsfield

- API: https://docs.higgsfield.ai/docs
- MCP: https://higgsfield.ai/mcp
- CLI: https://github.com/higgsfield-ai/cli

Use through an adapter. API/CLI/MCP are transports, not core domain types.

### HeyGen

- Developer platform: https://developers.heygen.com/
- CLI: https://developers.heygen.com/cli

Current developer material advertises REST/CLI/MCP and broad avatar/video capabilities. Verify the current authenticated **action** MCP surface before production reliance; the public docs MCP observed in prior research was read-oriented.

### Synthesia

- API: https://docs.synthesia.io/reference/introduction

Use REST/webhooks for asynchronous video generation. If an agent-facing MCP interface is useful, build a thin internal tool wrapper rather than assuming an undocumented vendor MCP.

### Google Veo

- Guide: https://ai.google.dev/gemini-api/docs/veo

Wrap long-running Gemini/Vertex generation behind the same creative-job interface. Do not leak Google SDK classes into canonical asset records.

### Open/local production

Evaluate open/local models and reusable production systems such as MoneyPrinterTurbo where they lower cost or improve control. They remain adapters.

## Provider choice

Support:

```text
capability-first:
  "Create 4 vertical image-to-video variants under this budget."

provider-explicit:
  "Use Veo because this shot requires the provider's supported control."
```

If a provider is explicitly requested for a unique feature, do not silently replace it with a materially different provider unless fallback policy allows it.

## Provenance

Every candidate render, not only the winner, should be traceable to:

- content/brief ID;
- parent and child agent runs;
- provider/model/tool version;
- normalized parameters and provider extension;
- reference assets;
- external job ID;
- cost/usage when available;
- output hash;
- rights/license notes;
- verifier/selection results.

This lets future analytics learn which creative tools/treatments actually perform.

## Safety boundary

Creative agents decide **what to try**. The runtime enforces credentials, budgets, tool permissions, policy/approval, artifact storage and external-effect rules.
