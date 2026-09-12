# Reference Research

Research snapshot: 2026-09-12. These projects are references or candidate components, not mandatory dependencies. Re-check versions, licenses, security and APIs before adopting them.

## Strong production-oriented building blocks

### n8n

- Repository: https://github.com/n8n-io/n8n
- Self-hosted AI starter kit: https://github.com/n8n-io/self-hosted-ai-starter-kit
- Relevant workflow: https://n8n.io/workflows/13528-create-ai-driven-social-media-posts-and-publish-to-all-major-platforms/
- Relevant trend workflow: https://n8n.io/workflows/4352-ai-powered-multi-social-media-post-automation-google-trends-and-perplexity-ai/

Why it matters: n8n demonstrates practical orchestration of scheduled triggers, research, AI generation, approval, API calls, retries and publishing. Its starter kit combines n8n, PostgreSQL, Qdrant and Ollama for self-hosted AI workflows. Community templates demonstrate trend discovery, competitor/content research, AI analysis, draft approval and multi-platform publishing.

Caveat: n8n is source-available/fair-code under its Sustainable Use License, not a conventional permissive open-source dependency. Keep our domain logic independent and treat n8n as an optional orchestration adapter unless the intended use is clearly compatible with its current license.

### MoneyPrinterTurbo

- Repository: https://github.com/harry0703/MoneyPrinterTurbo
- License: MIT at the time of this research.

Why it matters: it already turns a topic or script into voice, footage, subtitles, music and edited video. It exposes WebUI, API and CLI paths, supports Docker, multiple model providers/gateways, batch generation and optional publishing integrations. This makes it a strong candidate for a replaceable media-production worker rather than code we should immediately rewrite.

Design lesson: consume it through an adapter/API; keep content identity and workflow state in our own system.

### Postiz

- Repository: https://github.com/gitroomhq/postiz-app
- Agent/CLI: https://github.com/gitroomhq/postiz-agent

Why it matters: Postiz is a self-hosted social scheduling and analytics system with API/automation support. Its stack uses PostgreSQL and Temporal and it is designed for automation integrations. The agent/CLI project shows how agent-facing tools can create drafts, schedule posts, upload media and work with multiple integrations.

Caveat: the main repository currently uses AGPL-3.0. Check obligations before modifying, embedding or distributing it. Prefer an API adapter when that better matches the product/licensing model.

### LiteLLM

- Repository: https://github.com/BerriAI/litellm

Why it matters: it provides a self-hosted AI gateway with a unified interface across many model providers, plus routing, spend tracking, keys, logging, guardrails and load balancing. It is a strong reference for the provider abstraction we need.

Design lesson: even if LiteLLM is used, our internal task contracts should remain provider-neutral so the gateway itself can be replaced.

## Agent and workflow behavior references

### CrewAI — multi-agent and deterministic-flow reference

- Docs: https://docs.crewai.com/
- Concepts: https://docs.crewai.com/core-concepts/Agents

Why it matters: CrewAI explicitly separates **Crews** for autonomous role-based collaboration from **Flows** for structured, event-driven, stateful and deterministic orchestration. Its own guidance recommends combining them for hybrid applications where open-ended creative work needs reliable execution around it. Crew definitions are directly runnable in code (for example through crew kickoff), and CrewAI's managed AMP product can expose deployed crews through REST APIs; this is useful evidence that agent/team units benefit from simple callable surfaces.

Design conclusion: this strongly supports our architecture of a Lead/Supervisor plus callable specialists sitting above a deterministic durable runtime. We should copy the **product ergonomics and separation of concerns**, not make CrewAI's classes or hosted API our canonical domain model. CrewAI can be one runtime adapter if it wins the build-vs-reuse decision.

### OpenAI Agents SDK — supervisor/agents-as-tools reference

- Agents: https://openai.github.io/openai-agents-python/agents/
- Orchestration: https://openai.github.io/openai-agents-python/multi_agent/
- Tools/agents-as-tools: https://openai.github.io/openai-agents-python/tools/

Why it matters: current documentation describes a **manager / agents-as-tools** pattern in which one central agent retains responsibility and invokes specialists as callable tools, plus a separate handoff pattern where a specialist takes over. It also explicitly allows LLM-driven, code-driven or mixed orchestration.

Design conclusion: our default Lead-Agent behavior should be manager/supervisor style, while handoffs are optional. The source is a pattern reference only; canonical agent identity remains framework-neutral.

### A2A v1.0 — remote agent interoperability

- Home/specification: https://a2a-protocol.org/
- v1.0 overview/spec: https://a2a-protocol.org/latest/specification
- Agent concepts/cards/tasks: https://a2a-protocol.org/latest/topics/key-concepts/

Why it matters: A2A v1.0 is the stable open protocol for communication between independent agent systems. It defines discoverable Agent Cards/skills and stateful task lifecycles suitable for remote/opaque agents across frameworks and vendors. In August 2026 it joined the Agentic AI Foundation as a Growth Stage project.

Design conclusion: use our native contract for local agents; use A2A when a remote/vendor/independent agent needs real agent semantics. Do not make every internal call pay the protocol overhead.

### Model Context Protocol — tools/resources, not the whole agent fabric

- Specification: https://modelcontextprotocol.io/specification/2025-06-18
- Tools: https://modelcontextprotocol.io/specification/2025-06-18/server/tools

Why it matters: MCP standardizes tools, resources and prompts exposed to model/agent clients. Tools are model-controlled executable functions with schemas. This is ideal for browser/search/database/media/tool access.

Design conclusion: MCP is the default **vertical tool/context integration** reference. A bounded specialist can be projected as an MCP tool for convenience, but full independent agent collaboration should use our native agent contract or A2A.

### Temporal — durable execution reference

- Platform: https://temporal.io/
- Agentic AI: https://temporal.io/ai/agentic-ai

Why it matters: long-running autonomous agents are distributed systems. Temporal persists workflow state and provides retries, timers, task queues, pause/resume and recovery across process/network failures. Current 2026 material explicitly positions durable execution as complementary to agent frameworks rather than a replacement for their reasoning model.

Design conclusion: strongly evaluate Temporal before implementing our own workflow engine. Keep it behind `WorkflowBackend` so CrewAI Flows, another engine or a future runtime can replace it.

### Spec–Verifier–Environment attribution correction

The build discipline in this pack is useful, but its attribution must be precise.

- Karpathy LLM Wiki primary idea: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- Secondary scaffold explanation: https://guides.kno2gether.com/karpathy-method/
- Secondary three-layer explanation: https://www.vensas.de/en/blog/karpathy-three-layers

A current secondary guide explicitly states that **Spec -> Verifier -> Environment is a teaching scaffold built on Karpathy's broader ideas**, not a direct quote/framework name from him. Therefore this pack uses the phrase **"Karpathy-inspired Spec–Verifier–Environment discipline."**

The useful engineering content remains: state the outcome/invariants, define measurable verification, use the real repository/environment, and work in iterative verifiable slices instead of giant waterfall changes or human micro-instructions.



### LangChain Social Media Agent

- Repository: https://github.com/langchain-ai/social-media-agent
- License: MIT at the time of this research.

Why it matters: demonstrates a URL-to-social-content agent with human-in-the-loop approval, scheduled ingestion, authentication boundaries and structured agent state. It is a good reference for pausing/resuming workflows and separating generation from approval/publishing.

### Genesis Content OS

- Repository: https://github.com/DenisShokhirev041279/genesis-content-os

Why it matters: its architecture is unusually close to the target loop: scan trends, distill topics, generate channel content, ingest metrics, derive insights, change versioned prompts and measure whether the change improves real performance. The most useful design idea is the **measure-and-rollback loop**: proposed prompt/policy changes must beat a baseline using real metrics or be reverted.

Caveat: this is a small project and its README is partly self-reported. Treat it as an architecture/implementation reference, not proof of scale or business results.

## Browser and engineering-agent references

### Microsoft Playwright

- Repository: https://github.com/microsoft/playwright

Why it matters: Playwright provides deterministic browser automation across Chromium, Firefox and WebKit. Current Playwright documentation explicitly includes CLI/MCP paths for coding/AI agents, browser isolation, resilient locators, saved auth state, screenshots, tracing and network visibility.

Recommended role: default low-level browser automation layer where stable scripted behavior is possible.

### Browser Use

- Repository: https://github.com/browser-use/browser-use
- License: MIT at the time of this research.

Why it matters: provides higher-level model-driven browser interaction on top of browser automation concepts, including persistent sessions and MCP integration. Its own guidance is useful: use direct HTTP/API for simple public fetches and escalate to the browser when interaction, login, JavaScript rendering or protected UI is genuinely required.

Recommended role: semantic/adaptive browser worker for less predictable sites, behind strict policy and audit boundaries.

### OpenHands

- Repository: https://github.com/OpenHands/OpenHands
- SDK: https://github.com/OpenHands/software-agent-sdk
- License: MIT at the time of this research.

Why it matters: demonstrates an autonomous software-engineering worker that can edit files, run commands, browse the web and call APIs. It is a good open reference for "Devin-like" execution without making Devin a fixed dependency.

Recommended role: optional engineering/executor worker for building integrations, fixing code or performing bounded maintenance tasks. Do not let it become the product's single control plane.

### Appium

- Documentation: https://appium.io/

Why it matters: Appium is an open-source UI automation ecosystem supporting mobile, browser, desktop and other app platforms through drivers/plugins. It is relevant when a required operation truly exists only inside an app UI.

Recommended role: optional app-control adapter. Prefer API/browser integrations first because app UI automation is usually more brittle and operationally expensive.



## Verified creative-generation integrations (2026-09-12)

### Higgsfield

Official references:
- API docs: https://docs.higgsfield.ai/docs
- MCP: https://higgsfield.ai/mcp
- CLI repository: https://github.com/higgsfield-ai/cli
- CLI/Skills guide: https://higgsfield.ai/creator-hub/help-center/integrations/how-do-i-access-higgsfield-via-cli

Verified capabilities: Higgsfield exposes an authenticated asynchronous API for image/video generation; official MCP connects AI agents to image/video/character/audio capabilities; and the official CLI/Skills path is explicitly optimized for coding agents. The CLI supports structured JSON output and many creative models/workflows. API outputs are temporary enough that completed assets should be copied to our owned object storage.

Recommended role: a high-level `CreativeTool` adapter with API, CLI and MCP transports. Let the Lead Content Agent choose the transport based on environment. Prefer API for stable production jobs; CLI/MCP are valuable for agent-native interactive/iterative work. Treat credits/cost and model availability as live metadata.

### HeyGen

Official references:
- Developer platform: https://developers.heygen.com/
- CLI: https://developers.heygen.com/cli
- Public MCP endpoint inspected: https://developers.heygen.com/mcp

Verified capabilities: HeyGen's 2026 developer site advertises REST, CLI and MCP support plus avatars, voices, prompt-to-finished-video, translation, lip-sync, templates/composition and batch/webhook workflows. The CLI is explicitly intended for scripts, CI and AI-agent workflows and emits structured JSON.

Important nuance: the public `/mcp` endpoint inspected during this research identifies itself as a **read-only documentation MCP**. The developer homepage advertises agent-ready/action tooling, but the build agent must verify the current authenticated/action MCP path before relying on it for production generation. Until verified, treat HeyGen REST/CLI as the execution paths and MCP as optional.

### Synthesia

Official references:
- API docs: https://docs.synthesia.io/reference/introduction
- Create video: https://docs.synthesia.io/reference/create-video
- Quickstart: https://docs.synthesia.io/reference/synthesia-api-quickstart
- Agent-friendly docs index/OpenAPI note: https://docs.synthesia.io/

Verified capabilities: Synthesia exposes a production REST API for video creation, templates, assets, webhooks and asynchronous completion. Current docs state API access is on Creator or Enterprise plans and publish explicit rate limits. Its documentation is agent-friendly through `llms.txt` and OpenAPI.

No official action MCP was found in this review. Do not invent one. Integrate via REST/webhooks and, if an MCP surface is useful to the Lead Content Agent, expose **our own thin MCP/tool adapter** over the Synthesia API.

### Google Veo

Official references:
- Veo generation guide: https://ai.google.dev/gemini-api/docs/veo
- Gemini API: https://ai.google.dev/gemini-api/docs

Verified capabilities: Veo 3.1 is callable programmatically through the Gemini API. Current documentation supports asynchronous video generation with native audio, landscape/portrait output, multiple resolutions, reference-image guidance, first/last-frame control and video extension. Generation uses long-running operations that must be polled or otherwise reconciled before downloading the asset.

Recommended role: direct `CreativeTool`/`VideoGenerator` adapter through the Gemini API (and optionally Vertex AI when operational requirements favor it). Do not make Google's SDK types canonical. A local MCP/CLI wrapper may be added for agent ergonomics, but the core contract should remain transport-neutral.

### Architecture conclusion from these tools

Do **not** create a single hard-coded "video generator" implementation. Expose a capability catalog such as:

```text
generate_image
generate_video_text
generate_video_from_image
generate_video_with_references
extend_video
avatar_video
text_to_speech
voice_clone
lipsync
dub_or_translate
compose_or_edit_video
generate_music_or_sfx
caption
upscale
analyze_creative
```

Each provider advertises which capabilities it supports plus quality tier, formats/aspect ratios, expected latency, price/credit hints, concurrency/rate limits, maximum duration, async/webhook support and credential requirements. The Lead Content Agent chooses or requests a provider based on creative intent and constraints; the runtime validates permissions/budget and executes the call.


## Reuse-first component selection policy

Before implementing any non-trivial subsystem, search the current ecosystem for a free/open/self-hostable fit. The goal is not to maximize dependency count; it is to avoid rebuilding mature commodity infrastructure while keeping the product replaceable.

Evaluate candidates on:

- current license and commercial/self-hosting fit;
- maintenance activity, release history and security posture;
- real source/tests/issues rather than README claims alone;
- stable API/CLI/SDK or protocol surface;
- Docker/Linux deployment quality;
- data ownership and exportability;
- authentication and least-privilege support;
- observability, retries and failure behavior;
- resource usage and expected operating cost;
- extensibility/plugin support;
- ability to run behind our adapter without leaking its data model into core code;
- credible community/adoption signals and availability of alternatives.

Preferred adoption order:

```text
1. Use an existing protocol/API/service through our adapter
2. Use an upstream extension/plugin mechanism
3. Compose a proven library inside one bounded adapter/service
4. Maintain a small patch or light fork only when necessary
5. Build from scratch when reuse is a worse long-term engineering choice
```

A popular project is not automatically the right choice. Reject a dependency when licensing, security, abandonment risk, operational complexity, hidden cloud coupling or provider lock-in outweighs the amount of code saved. When a custom implementation is chosen, record why it is expected to be simpler/cheaper to own.

For every adopted component, record an exit path: replacement interface, owned data location, export/migration plan and fallback behavior.

## Research-derived architecture conclusions

1. The target is correctly a **multi-agent autonomous system with one accountable Lead/Supervisor** and first-class callable specialists. CrewAI's Crews+Flows and the OpenAI manager/agents-as-tools pattern independently support this hybrid direction.
2. Specialist agents should be logical callable units, not mandatory separate model deployments. The same model can back many agents; use separate agents where delegation, parallelism, isolated context, independent review or long tool loops add value.
3. The deterministic runtime belongs underneath the agent layer. Durable state, retries, timers, idempotency, permissions and recovery are distributed-systems concerns; Temporal is a strong existing solution to evaluate rather than rebuilding them casually.
4. **MCP and A2A are complementary, not substitutes**: MCP for tools/resources; A2A for independent remote agents. Our native internal contract remains the simplest local path.
5. CrewAI-like user ergonomics should be supported through our own stable callable-agent API/SDK/CLI. CrewAI itself remains an optional adapter/reference so future framework changes do not rewrite the product.
6. The market already contains mature pieces for media creation, publishing, browser automation, model routing and workflows. Compose/adapt before rebuilding.
7. Browser automation is a normal agent capability, but deterministic browser/API integrations should replace recurring unconstrained browsing when possible.
8. Human approval and resumable state remain useful for sensitive social publishing, but normal low-risk operation may be autonomous under policy.
9. Real analytics should close the loop back to strategy/content decisions. Self-improvement must be versioned, measured and reversible.
10. License fit is architectural. n8n's Sustainable Use License and Postiz's AGPL obligations require explicit review before embedding/distribution.
11. The exact phrase "Spec -> Verifier -> Environment" is kept only as a **Karpathy-inspired teaching/engineering scaffold**, not presented as a verified official framework authored under that name.

## Research rule for the build agent

Do not assume this snapshot is current forever. Before integrating a project, inspect its current repository, latest stable release, license file, security policy/advisories, API docs, maintenance activity and deployment requirements. Record the exact commit/release used in `docs/dependencies.md`.

Also search for newer/better alternatives before committing to a major subsystem. Record the candidates considered, the build-vs-reuse decision, why the chosen option won, its adapter boundary, and how it can be replaced later.
