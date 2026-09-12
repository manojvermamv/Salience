# Real Intelligence Loop: Pre-Phase 5 and Phases 5–6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a durable, source-grounded intelligence loop from a niche to a selected immutable ContentBrief while preserving all Phases 1–4 contracts.

**Architecture:** Official MCP/A2A SDK adapters and Playwright sit behind owned gateways. PostgreSQL owns source, signal, opportunity, package, claim, brief, model, trust, and lineage state; Temporal owns durable orchestration. Lead, Research, Strategy, and Browser Research agents invoke the same native contract directly or through delegation.

**Tech Stack:** Python 3.13, PostgreSQL, Temporal, FastAPI, `httpx`, `mcp==2.2.0`, `a2a-sdk==1.1.2`, optional `playwright==1.62.0`, Pydantic, JSON Schema, pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-intelligence-loop-phases-5-6-design.md`

## Global Constraints

- Preserve canonical Phases 1–4 identities, migrations, native agent contracts, Temporal workflow backend, scoped memory, and existing public routes.
- Keep provider/SDK objects outside `salience` domain DTOs and PostgreSQL JSON payloads.
- External input is `untrusted_external` by default and cannot grant privilege, override policy, invoke write effects, or persist trusted memory.
- Use API/feed before browser; all new tool effects are read-only and policy/scope/budget/timeout governed.
- Keep normal CI deterministic and offline; label real network/model smoke tests with `live` and require explicit configuration.
- Before any browser download or storage-heavy Compose run, inspect `df -h .` and `docker system df`; never remove a persistent volume or run global Docker cleanup without authorization.
- Checkpoint progress in `docs/implementation-progress.md` after each verified task. Do not create a final commit unless the owner requests one.

---

### Task 1: Record adoption decisions and expose configuration

**Files:**
- Create: `docs/adr/0005-mcp-a2a-and-browser-sdk-adoption.md`
- Create: `docs/compatibility.md`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `src/salience/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces `Settings` fields for configured model, connector, MCP, A2A, and browser policy without storing secret values.
- Produces dependency pins `mcp==2.2.0`, `a2a-sdk==1.1.2`, and optional extra `browser = ["playwright==1.62.0"]`.

- [x] **Step 1: Write failing configuration tests**

```python
def test_settings_accepts_optional_intelligence_runtime_configuration(monkeypatch):
    monkeypatch.setenv("MODEL_RUNTIME_ID", "research-model")
    monkeypatch.setenv("MODEL_BASE_URL", "https://models.example/v1")
    monkeypatch.setenv("MODEL_SECRET_REF", "secret://models/research")
    monkeypatch.setenv("RESEARCH_ALLOWED_DOMAINS", "news.example,feeds.example")
    settings = Settings.from_environment()
    assert settings.model_runtime_id == "research-model"
    assert settings.research_allowed_domains == ("news.example", "feeds.example")
```

- [x] **Step 2: Run the focused configuration test and observe failure**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/test_config.py -q`

- [x] **Step 3: Add exact optional settings and pinned dependencies**

Add explicit `Settings` fields for model base URL/model/runtime ID/secret reference, source request timeout/byte limit, comma-separated allowed domains, browser enabled flag, browser timeout/step limit, and protocol endpoint/auth secret reference. Parse lists into immutable tuples and reject invalid non-positive limits. Add a `browser` optional dependency group so normal installation never downloads browser binaries.

- [x] **Step 4: Document the build-vs-adopt decision**

The ADR records MCP 2.2.0/MIT/`2026-07-28`, A2A SDK 1.1.2/Apache-2.0/A2A 1.0 plus 0.3 compatibility, and Playwright 1.62.0/Apache-2.0. For each, record used extensions, auth behavior, operating cost, fallback, and owned-contract exit path. `docs/compatibility.md` contains the four exact matrix rows from the spec.

- [x] **Step 5: Re-run focused configuration tests and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/test_config.py -q`

Record the verified result in `docs/implementation-progress.md`.

### Task 2: Replace handwritten protocol paths with official SDK adapters

**Files:**
- Create: `src/salience/mcp/sdk_adapter.py`
- Create: `src/salience/a2a/sdk_adapter.py`
- Modify: `src/salience/mcp/contracts.py`
- Modify: `src/salience/mcp/gateway.py`
- Modify: `src/salience/a2a/contracts.py`
- Modify: `src/salience/a2a/gateway.py`
- Test: `tests/integration/test_mcp_sdk_compatibility.py`
- Test: `tests/integration/test_a2a_sdk_compatibility.py`

**Interfaces:**
- `McpSdkAdapter.discover(server_id, tool_name) -> ToolManifest` and `invoke(tool_name, arguments) -> dict[str, object]` implement `McpToolServer`.
- `A2ASdkRemoteAdapter.descriptor() -> RemoteAgentDescriptor` and `invoke(input) -> tuple[dict[str, object], list[dict[str, object]]]` implement `A2ARemoteAgent`.
- `ToolManifest` and `RemoteAgentDescriptor` gain owned compatibility metadata only; no SDK classes escape.

- [x] **Step 1: Add failing protocol compatibility tests**

```python
async def test_mcp_sdk_adapter_discovers_and_calls_2026_tool():
    adapter = running_mcp_2026_fixture_adapter()
    gateway = ToolGateway(server=adapter, allowed_scopes=frozenset({"research.fetch"}),
                          supported_protocol_version="2026-07-28")
    manifest = await gateway.discover("fixture", "research.fetch")
    assert (await gateway.invoke(manifest, {"query": "climate"})).protocol_version == "2026-07-28"

async def test_a2a_sdk_adapter_preserves_1_0_task_lineage():
    gateway = RemoteAgentGateway(agent=running_a2a_1_0_fixture(), supported_protocol_version="1.0")
    descriptor = await gateway.discover("fixture-agent")
    result = await gateway.invoke(descriptor, {"niche": "gardening"}, uuid4())
    assert result.protocol_version == "1.0"
```

- [x] **Step 2: Run both tests and observe import/adapter failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/integration/test_mcp_sdk_compatibility.py tests/integration/test_a2a_sdk_compatibility.py -q`

- [x] **Step 3: Implement thin SDK adapters**

Use MCP v2 discovery/call APIs and project names, schemas, protocol revisions, auth mode, and response provenance into `ToolManifest`/`ToolResult`. Use the A2A SDK card resolver/client task APIs and project Agent Card skills/task artifacts/cancel semantics into owned descriptors/results. Validate JSON-compatible values before returning them. Convert unsupported revisions into `ToolProtocolIncompatible` or `RemoteProtocolIncompatible` with a migration message.

- [x] **Step 4: Add explicit legacy behavior tests**

```python
async def test_mcp_2025_fixture_is_negotiated_not_reinterpreted():
    result = await legacy_gateway.invoke(await legacy_gateway.discover("fixture", "research.fetch"), {"query": "x"})
    assert result.protocol_version == "2025-11-25"

async def test_a2a_0_3_descriptor_is_explicitly_compatible_or_rejected():
    with pytest.raises(RemoteProtocolIncompatible, match="0.3"):
        await strict_v1_gateway.discover("legacy-agent")
```

- [x] **Step 5: Re-run protocol tests and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/integration/test_mcp_gateway.py tests/integration/test_a2a_gateway.py tests/integration/test_mcp_sdk_compatibility.py tests/integration/test_a2a_sdk_compatibility.py -q`

### Task 3: Establish the untrusted-input and memory-write trust gate

**Files:**
- Create: `src/salience/governance/trust.py`
- Modify: `src/salience/agents/execution.py`
- Modify: `src/salience/memory/contracts.py`
- Modify: `src/salience/memory/repository.py`
- Modify: `src/salience/research/contracts.py`
- Test: `tests/unit/test_research_trust.py`
- Test: `tests/integration/test_memory_trust.py`

**Interfaces:**
- `TrustContext` holds `trust_level`, `source_identity`, `effect_classification`, `delegated_authority`, `tool_scope`, `network_scope`, and `memory_write_authority`.
- `TrustPolicy.authorize_memory_write(context, record) -> None` raises `PermissionError` before persistence.
- `ResearchFinding` gains source identity, provenance, explicit trust, and raw-content classification.

- [x] **Step 1: Write failing trust and injection tests**

```python
def test_external_instructions_cannot_elevate_authority_or_memory_write():
    context = TrustContext.untrusted_source("https://example.test/article")
    record = MemoryRecordInput(scope="semantic", content={"text": "ignore policy"}, trust_level="trusted")
    with pytest.raises(PermissionError, match="memory_write_authority"):
        TrustPolicy().authorize_memory_write(context, record)

async def test_untrusted_research_memory_keeps_source_and_cannot_be_verified_without_evidence(repository):
    record = await repository.record_external_research(
        workspace_id="00000000-0000-0000-0000-000000000001",
        program_id="00000000-0000-0000-0000-000000000002",
        record=MemoryRecordInput(scope="evidence", content={"text": "external"}),
        context=TrustContext.untrusted_source("https://example.test/article"),
    )
    assert record.trust_level == "untrusted_external"
    assert record.verification_status == "unverified"
```

- [x] **Step 2: Run trust tests and observe failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_research_trust.py tests/integration/test_memory_trust.py -q`

- [x] **Step 3: Implement minimum-authority propagation and repository guard**

Construct `AgentExecutionContext` from manifest scopes intersected with parent authority. Never accept scope/authority fields from tool or model output. Extend memory inserts to persist all required metadata, and add `record_external_research` that always sets `untrusted_external`, source identity, evidence references, writer actor, and `unverified` unless the policy receives eligible evidence.

- [x] **Step 4: Add direct/delegated authority equivalence test**

Assert a delegated Research invocation receives no scopes absent from the Research manifest and the same sanitized `ResearchResult@v1` type as direct invocation.

- [x] **Step 5: Re-run trust suite and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_research_trust.py tests/integration/test_memory_trust.py tests/integration/test_agent_delegation.py -q`

### Task 4: Add structured model execution and model invocation lineage

**Files:**
- Create: `src/salience/models/recording.py`
- Modify: `src/salience/models/contracts.py`
- Modify: `src/salience/models/openai_compatible.py`
- Modify: `src/salience/models/static.py`
- Modify: `src/salience/agents/execution.py`
- Test: `tests/contracts/test_structured_model_gateway.py`
- Test: `tests/integration/test_model_invocations.py`

**Interfaces:**
- `ModelRequest` adds `capability`, `parent_agent_run_id`, `job_id`, `trace_context`, and optional input artifact reference.
- `RecordedModelGateway.complete(request) -> ModelResult` records success/failure in `model_invocations`.
- `ModelResult` includes provider/model/version, hashes/artifact references, actual-cost value, and a validated JSON output.

- [x] **Step 1: Write failing structured-output tests**

```python
async def test_invalid_model_json_is_recorded_and_cannot_become_agent_output():
    with pytest.raises(ModelOutputInvalidError):
        await gateway.complete(ModelRequest(prompt="x", output_schema={"required": ["topic"]}))
    invocation = await repository.latest()
    assert invocation.status == "invalid_output"

async def test_openai_compatible_usage_and_lineage_are_recorded(mock_server):
    result = await gateway.complete(model_request(parent_agent_run_id=uuid4()))
    assert result.provider_metadata["model"] == "fixture-model"
    assert (await repository.latest()).parent_agent_run_id is not None
```

- [x] **Step 2: Run focused model tests and observe failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/contracts/test_model_gateway.py tests/contracts/test_structured_model_gateway.py tests/integration/test_model_invocations.py -q`

- [x] **Step 3: Implement schema validation and recording decorator**

Hash canonical serialized input/output, map OpenAI-compatible usage fields when present, and record failures in a `finally`-safe path that redacts credentials. Validate before `AgentService` constructs an `AgentRun`; preserve static adapter behavior for fixture schemas.

- [x] **Step 4: Re-run model tests and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/contracts/test_model_gateway.py tests/contracts/test_structured_model_gateway.py tests/integration/test_model_invocations.py tests/integration/test_runtime_swap.py -q`

### Task 5: Create research connectors and governed browser contract

**Files:**
- Create: `src/salience/research/http.py`
- Create: `src/salience/research/rss.py`
- Create: `src/salience/research/hacker_news.py`
- Create: `src/salience/browser/contracts.py`
- Create: `src/salience/browser/playwright.py`
- Modify: `src/salience/research/contracts.py`
- Test: `tests/unit/test_research_connectors.py`
- Test: `tests/unit/test_browser_policy.py`
- Test: `tests/live/test_research_smoke.py`

**Interfaces:**
- `ResearchSourceConnector.fetch(request: SourceFetchRequest) -> list[FetchedSource]`.
- `BrowserResearchTool.read(request: BrowserResearchRequest) -> BrowserResearchResult`.
- `FetchedSource` carries source/version/type/cursor/resource identity/URL/times/raw hash/trust/rate limit/provenance.

- [x] **Step 1: Write failing local-server connector tests**

```python
async def test_rss_connector_preserves_guid_pubdate_and_raw_hash(rss_server):
    items = await RssAtomConnector(client).fetch(feed_request(rss_server.url))
    assert items[0].raw_identity == "guid-42"
    assert items[0].published_at is not None

async def test_hn_connector_maps_item_id_and_available_engagement(hn_server):
    item = (await HackerNewsConnector(client).fetch(hn_request(hn_server.url)))[0]
    assert item.raw_identity == "123"
    assert item.features["engagement"] == 50
```

- [x] **Step 2: Run connector tests and observe failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_research_connectors.py tests/unit/test_browser_policy.py -q`

- [x] **Step 3: Implement direct HTTP connectors and policy enforcement**

Use `httpx.AsyncClient` with fixed timeout, redirects disabled until each redirect is allowlist-checked, response byte cap, content-type validation, request fingerprint, and retry-after parsing. Parse RSS/Atom with `xml.etree.ElementTree`; map HN's documented `v0/item/{item_id}.json` fields without inventing unavailable metrics.

- [x] **Step 4: Implement optional Playwright adapter**

Import Playwright lazily and fail with a clear `BrowserUnavailable` when the optional package/binary is absent. Create an incognito context, route-abort non-allowlisted requests/downloads, use only `page.goto` and bounded text extraction, write screenshot/trace bytes through `ObjectStore`, and return artifact hashes/URLs rather than raw browser objects.

- [x] **Step 5: Add malformed/blocked browser tests and checkpoint**

```python
async def test_browser_blocks_redirect_to_unapproved_domain(browser_fixture):
    with pytest.raises(NetworkScopeDenied):
        await tool.read(BrowserResearchRequest(url="https://allowed.test/redirect"))
```

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_research_connectors.py tests/unit/test_browser_policy.py -q`

### Task 6: Add the canonical intelligence-loop migration and repositories

**Files:**
- Create: `migrations/versions/0004_intelligence_loop.py`
- Create: `src/salience/intelligence/contracts.py`
- Create: `src/salience/intelligence/repository.py`
- Modify: `src/salience/db/models.py`
- Modify: `src/salience/bootstrap/repository.py`
- Modify: `src/salience/memory/repository.py`
- Test: `tests/integration/test_intelligence_migrations.py`
- Test: `tests/integration/test_intelligence_repository.py`

**Interfaces:**
- Repository methods: `record_source`, `record_fetch`, `record_evidence`, `record_signal`, `link_signal_support`, `record_opportunity`, `record_package`, `record_evaluation`, `record_claim`, `link_claim_evidence`, and `record_content_brief`.
- All record methods accept workspace/program IDs, trace/provenance, and an idempotency key where a retry can occur.

- [x] **Step 1: Write failing migration invariants**

```python
async def test_intelligence_migration_protects_full_brief_lineage(database_url):
    await apply_migrations(database_url)
    assert {"research_sources", "research_fetches", "signals", "signal_support",
            "topic_opportunities", "strategic_packages", "package_evaluations",
            "claims", "claim_evidence", "content_brief_versions", "model_invocations"} <= await list_tables(database_url)
    assert "uq_research_fetch_source_resource_window" in await list_constraint_names(database_url)
```

- [x] **Step 2: Run migration tests and observe failure**

Run:

```bash
postgres_id=$(docker compose ps -q postgres)
postgres_ip=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$postgres_id")
TEST_DATABASE_URL="postgresql://salience:salience@${postgres_ip}:5432/salience" \
  .worktrees/phases-1-4/.venv/bin/pytest tests/integration/test_intelligence_migrations.py -q
```

- [x] **Step 3: Implement append-only canonical schema**

Create the exact entities/foreign keys/indexes from the spec. Extend `research_evidence` and `memory_records` with additive nullable columns only. Use database uniqueness for fetch fingerprint/window, signal support, and brief version. Include safe downgrade ordering. Add typed SQLAlchemy models that match the migration but retain Psycopg repositories as the active persistence boundary.

- [x] **Step 4: Implement idempotent repository writes**

Use exact idempotent inserts such as:

```sql
INSERT INTO research_fetches (
    workspace_id, content_program_id, research_source_id, resource_identity,
    window_key, request_fingerprint, status
) VALUES (%s, %s, %s, %s, %s, %s, 'succeeded')
ON CONFLICT (research_source_id, resource_identity, window_key)
DO UPDATE SET updated_at = CURRENT_TIMESTAMP
RETURNING id::text;
```

Store raw content hash and canonical URL separately. Verify a retry returns the
original IDs and preserves every source support row.

- [x] **Step 5: Re-run migration/repository tests and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/integration/test_migrations.py tests/integration/test_intelligence_migrations.py tests/integration/test_intelligence_repository.py -q`

### Task 7: Implement Phase 5 signal normalization, deduplication, and ranking

**Files:**
- Create: `src/salience/intelligence/signals.py`
- Create: `src/salience/intelligence/scoring.py`
- Test: `tests/unit/test_signal_normalization.py`
- Test: `tests/unit/test_opportunity_ranking.py`
- Test: `tests/evals/test_phase5_signal_eval.py`

**Interfaces:**
- `normalize_fetched_source(source) -> CanonicalSignalInput`.
- `SignalDeduplicator.merge(signals) -> list[MergedSignal]`.
- `OpportunityRanker.rank(signals, model_judgements) -> list[TopicOpportunityInput]`.

- [x] **Step 1: Write failing normalization/dedup/ranking tests**

```python
def test_duplicate_resource_observations_merge_and_keep_all_supports():
    merged = SignalDeduplicator().merge([same_resource("rss"), same_resource("hn")])
    assert len(merged) == 1
    assert {support.source_id for support in merged[0].supports} == {"rss", "hn"}

def test_ranker_never_invents_missing_metrics_and_explains_score():
    opportunity = OpportunityRanker().rank([signal_without_engagement()], [])[0]
    assert "engagement" not in opportunity.features
    assert "engagement" in opportunity.feature_availability["missing"]
```

- [x] **Step 2: Run the signal tests and observe failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_signal_normalization.py tests/unit/test_opportunity_ranking.py tests/evals/test_phase5_signal_eval.py -q`

- [x] **Step 3: Implement deterministic normalizers and merge keys**

Normalize canonical URLs, titles, publication timestamps, explicit source identity, and provided metrics. Use resource identity first, then stable normalized title/topic fingerprint. Preserve per-field availability. Never synthesize engagement/velocity/freshness from an absent field.

- [x] **Step 4: Implement transparent ranking plus bounded semantic adjustment**

Implement the spec formula, clamp output to `[0, 100]`, and accept a schema-valid semantic adjustment only in `[-10, 10]`. Persist base score, adjustment, all feature values, availability, explanation, risks, and rejected candidates.

- [x] **Step 5: Re-run Phase 5 evals and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_signal_normalization.py tests/unit/test_opportunity_ranking.py tests/evals/test_phase5_signal_eval.py -q`

### Task 8: Make Research, Strategy, and Browser Research agents production-capable

**Files:**
- Modify: `src/salience/agents/fixtures.py`
- Modify: `src/salience/agents/specialists.py`
- Create: `src/salience/agents/intelligence.py`
- Modify: `src/salience/agents/lead.py`
- Modify: `src/salience/api/schemas.py`
- Test: `tests/unit/test_intelligence_agents.py`
- Test: `tests/integration/test_direct_delegated_intelligence_agents.py`

**Interfaces:**
- `ResearchAgentRuntime`, `StrategyAgentRuntime`, and `BrowserResearchAgentRuntime` implement `AgentRuntime`.
- `LeadContentAgent.run_intelligence(request) -> AgentRun` owns parent/child decisions.
- Output schemas are `ResearchResult@v1`, `StrategyProposal@v1`, and `BrowserResearchResult@v1`.

- [x] **Step 1: Write failing direct/delegated contract tests**

```python
async def test_research_agent_direct_and_delegated_outputs_match_schema(service):
    direct = await service.invoke(AgentInvocation("research_agent", {"niche": "home gardening"}))
    delegated = await service.invoke_from_parent("lead_content_agent", AgentInvocation("research_agent", {"niche": "home gardening"}))
    assert direct.output.keys() == delegated.output.keys()
    assert delegated.parent_run_id is not None
```

- [x] **Step 2: Run agent tests and observe fixture-only failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_intelligence_agents.py tests/integration/test_direct_delegated_intelligence_agents.py -q`

- [x] **Step 3: Implement runtime injection and structured agent behavior**

Inject connectors, repositories, and `StructuredModelGateway` into runtimes. Research returns source-linked findings, contradicting evidence, and unresolved questions only. Strategy consumes persisted evidence/signals/opportunities and returns a proposal with audience, positioning, pillars, channels, topic priorities, cadence, metrics, assumptions, and uncertainties. Browser agent is read-only and returns artifacts/lineage.

- [x] **Step 4: Add model-invalid and unavailable-provider behavior tests**

Assert `ModelOutputInvalidError` and unavailable runtime failures leave no strategy/opportunity canonical state, while their invocation/failure audit data persists.

- [x] **Step 5: Re-run agent tests and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_intelligence_agents.py tests/integration/test_direct_delegated_intelligence_agents.py tests/unit/test_agent_execution.py -q`

### Task 9: Build the durable Phase 5 intelligence workflow and public controls

**Files:**
- Create: `src/salience/workflows/intelligence.py`
- Modify: `src/salience/workflows/worker.py`
- Modify: `src/salience/workflows/schedules.py`
- Modify: `src/salience/api/routes/control.py`
- Create: `src/salience/api/routes/intelligence.py`
- Modify: `src/salience/api/app.py`
- Modify: `src/salience/cli.py`
- Modify: `src/salience/sdk/client.py`
- Test: `tests/integration/test_intelligence_control_api.py`
- Test: `tests/e2e/test_phase5_durable_research.py`

**Interfaces:**
- Temporal workflow `IntelligenceLoopWorkflow.run(request: IntelligenceRunRequest) -> IntelligenceRunResult`.
- API `POST /v1/intelligence/runs`, `GET /v1/intelligence/runs/{job_id}`, and `POST /v1/intelligence/schedules`.
- CLI `content intelligence start`, `content intelligence inspect`, and `content intelligence schedule`.

- [x] **Step 1: Write failing workflow checkpoint/restart tests**

```python
async def test_intelligence_run_recovers_after_fetch_persistence_before_activity_return(runtime):
    result = await run_and_restart(runtime, crash_at="research.fetch.persisted")
    assert result.state == "completed"
    assert await repository.count_fetches(result.job_id) == 1
    assert await repository.count_briefs(result.program_id) == 0
```

- [x] **Step 2: Run Phase 5 workflow/control tests and observe failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/integration/test_intelligence_control_api.py tests/e2e/test_phase5_durable_research.py -q`

- [x] **Step 3: Implement activities, checkpoints, and cancellation**

Add activities for source fetch, evidence persistence, normalization/merge, ranking, Research/Strategy invocation, strategy proposal persistence, and queue update. Use canonical job ID/idempotency key in each activity. Checkpoint after every activity through `CanonicalJobStore`; use Temporal retry policy with bounded attempts/backoff/timeouts. Translate cancellation into no-new-work behavior plus canonical `cancelled` checkpoint.

- [x] **Step 4: Add direct API/SDK/CLI route tests**

Assert authenticated callers can start/inspect a dry-run intelligence job, receive the job/trace IDs, and that schedule creation stores an ordinary `intelligence_research` job schedule.

- [x] **Step 5: Re-run Phase 5 durable tests and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/integration/test_intelligence_control_api.py tests/e2e/test_phase5_durable_research.py tests/e2e/test_phase1_verifier.py -q`

### Task 10: Implement Phase 6 strategic packages, claims, and immutable briefs

**Files:**
- Create: `src/salience/intelligence/packages.py`
- Create: `src/salience/intelligence/claims.py`
- Create: `src/salience/intelligence/briefs.py`
- Modify: `src/salience/intelligence/repository.py`
- Modify: `src/salience/agents/intelligence.py`
- Test: `tests/unit/test_strategic_packages.py`
- Test: `tests/unit/test_claim_evidence.py`
- Test: `tests/integration/test_content_brief_repository.py`
- Test: `tests/evals/test_phase6_packaging_eval.py`

**Interfaces:**
- `StrategicPackageGenerator.generate(opportunity, strategy, count) -> list[StrategicPackageInput]`.
- `PackageEvaluator.evaluate(package, evidence, strategy) -> PackageEvaluationInput`.
- `ClaimVerifier.link(claim, evidence) -> ClaimVerification`.
- `ContentBriefAssembler.create(program_id: str, opportunity: TopicOpportunity,
  package: StrategicPackage, strategy_id: str, claims: list[Claim]) -> ContentBriefInput`.

- [x] **Step 1: Write failing package diversity and claim-link tests**

```python
def test_package_generator_rejects_trivially_duplicate_hooks():
    candidates = generator.generate(opportunity(), strategy(), count=3)
    assert len(candidates) == 3
    assert len({candidate.diversity_fingerprint for candidate in candidates}) == 3

def test_claim_with_only_contradictory_evidence_is_not_verified():
    status = ClaimVerifier().link(claim(), [contradicting_evidence()])
    assert status.verification_status == "contradicted"
```

- [x] **Step 2: Run Phase 6 unit/eval tests and observe failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_strategic_packages.py tests/unit/test_claim_evidence.py tests/evals/test_phase6_packaging_eval.py -q`

- [x] **Step 3: Implement candidate generation/evaluation and evidence rules**

Generate 3–5 candidates with required audience, problem/desire, angle, hook, promise, format, duration, opening concept, novelty, evidence needs, and risks. Build fingerprints from normalized semantic fields; reject high-overlap candidates with durable reasons. Apply deterministic checks and bounded schema-valid semantic scores. Preserve all candidates/evaluations and record the selected-package reason.

- [x] **Step 4: Implement immutable brief assembly**

Create a new version per program/brief key. Include selected opportunity/package, strategy reference, required/unsupported claims, evidence/contradictions, sources, risks/questions, and recommended Phase-7 action. Refuse a brief if selected claims are unverified/contradicted unless explicitly listed as prohibited or unresolved.

- [x] **Step 5: Re-run Phase 6 unit/eval/repository tests and checkpoint**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/unit/test_strategic_packages.py tests/unit/test_claim_evidence.py tests/integration/test_content_brief_repository.py tests/evals/test_phase6_packaging_eval.py -q`

### Task 11: Complete the durable selected-topic-to-brief path and end-to-end evals

**Files:**
- Modify: `src/salience/workflows/intelligence.py`
- Modify: `src/salience/api/routes/intelligence.py`
- Modify: `src/salience/api/schemas.py`
- Modify: `src/salience/cli.py`
- Modify: `src/salience/sdk/client.py`
- Test: `tests/e2e/test_phases_5_6_intelligence_loop.py`
- Test: `tests/evals/test_intelligence_safety_eval.py`

**Interfaces:**
- `IntelligenceLoopWorkflow` accepts optional selected opportunity or runs deterministic selection, then produces `content_brief_id`.
- API `POST /v1/intelligence/opportunities/{opportunity_id}/briefs` uses `ContentBriefRequest@v1`.

- [x] **Step 1: Write the complete lineage verifier**

```python
async def test_niche_to_selected_content_brief_has_complete_lineage(stack):
    run = await stack.start_intelligence(niche="urban gardening")
    brief = await stack.wait_for_content_brief(run.job_id)
    lineage = await stack.repository.lineage_for_brief(brief.id)
    assert lineage.source_ids and lineage.fetch_ids and lineage.signal_ids
    assert lineage.opportunity_id == brief.opportunity_id
    assert lineage.package_id == brief.selected_package_id
    assert lineage.lead_agent_run_id == run.lead_agent_run_id
```

- [x] **Step 2: Write fault-injection evals**

Create fixtures for invalid model JSON, provider unavailable, source timeout, repeated source event, conflicting evidence, budget exhaustion, permission denial, prompt injection text, malformed browser output, and cancellation. Each asserts no unsupported trusted memory/brief state and a durable inspection/audit/provenance record.

- [x] **Step 3: Run E2E/eval tests and observe current failures**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/e2e/test_phases_5_6_intelligence_loop.py tests/evals/test_intelligence_safety_eval.py -q`

- [x] **Step 4: Wire Phase 6 activities and public inspection**

Add package generation/evaluation, selection, evidence/claim validation, and brief persistence activities to the same Temporal job. Expose brief/lineage inspection through the intelligence route/SDK/CLI. Reuse the existing job audit/provenance/trace/cost inspection model; do not add a second workflow engine or scheduler.

- [x] **Step 5: Re-run end-to-end and existing verifier slices**

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/e2e/test_phase5_durable_research.py tests/e2e/test_phases_5_6_intelligence_loop.py tests/evals/test_phase5_signal_eval.py tests/evals/test_phase6_packaging_eval.py tests/evals/test_intelligence_safety_eval.py -q`

### Task 12: Document operations, verification, and Phase-7 handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/deployment.md`
- Modify: `docs/verification.md`
- Modify: `docs/limitations.md`
- Modify: `docs/implementation-progress.md`
- Create: `docs/research.md`
- Create: `docs/trust-model.md`
- Create: `docs/model-execution.md`
- Create: `docs/phase-7-handoff.md`
- Test: `tests/e2e/test_phases_1_4_stack.py`

**Interfaces:**
- Documents exact deterministic verification command, `live` smoke command, optional browser installation command, source configuration, policy scopes, and ContentBrief schema/lineage.

- [x] **Step 1: Write documentation assertions/checklist**

Verify docs state that MCP/A2A SDK revisions, source connectors, browser constraints, trust/memory rules, model capture, Phase 5 signal/strategy lifecycle, Phase 6 package/claim/brief lifecycle, and all deferred Phase-7 capabilities are explicit.

- [x] **Step 2: Update only delivered behavior**

Document the API/feed-first source order, Playwright optional runtime install, default read-only policy, no secret values in config, current compatibility matrix, deterministic fixture command, and opt-in environment variables for live smoke tests. Define `ContentBriefVersion` as the only Phase-7 input.

- [x] **Step 3: Run focused docs and legacy verifier checks**

Run: `git diff --check`

Run: `.worktrees/phases-1-4/.venv/bin/pytest tests/e2e/test_phases_1_4_stack.py -q`

- [x] **Step 4: Run the full permitted verification matrix**

Run deterministic unit/contracts/integration/eval tests first. Inspect disk before invoking Compose/browser tests. If capacity permits, run `bash scripts/verify-phases-1-4.sh` plus Phase 5–6 E2E. If capacity does not permit, record `df -h .`, `docker system df`, the skipped command, and the exact capacity/remediation needed; do not claim it passed.

- [x] **Step 5: Persist the final checkpoint**

Update `docs/implementation-progress.md` with each command/result, protocol versions, dependency decision, known environment limitation, and the exact Phase-7 starting record. Leave the worktree status available for owner review.
