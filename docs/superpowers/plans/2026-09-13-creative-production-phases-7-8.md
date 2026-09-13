# Creative Production Phases 7–8 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a restart-safe, provider-neutral, governed path from immutable `ContentBrief@v1` to immutable `ReadyToPublishPackage@v1`, without publishing, analytics, experiments, or learning.

**Architecture:** Create a `salience.creative` domain package for Phase 7–8 DTOs, idempotent persistence, providers, media, deterministic gates, and final-package assembly. A Temporal creative workflow composes the existing native agent service, plugin registry, cost/policy substrate, object store, and canonical checkpoints; it never rewrites Phase 5–6 data.

**Tech Stack:** Python 3.13, Pydantic 2.12, PostgreSQL/Alembic/Psycopg 3.3, Temporal 1.32, existing `httpx` 0.28, existing object-store contracts, host `ffmpeg`/`ffprobe` when available, optional `c2patool`, deterministic fixtures, pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-creative-production-phases-7-8-design.md`

## Global Constraints

- Take an exact existing `ContentBrief@v1`; do not re-rank, mutate, or recreate Phase 5–6 opportunity, package, claim, or evidence records.
- Persist canonical IDs, hashes, safe metadata, and secret references only; never persist credentials, temporary-provider URL credentials, or C2PA private keys.
- Give every provider submission a stable idempotency key, then reconcile its external ID before any retry.
- Default routes and workflows are dry-run and fixture-backed. There is no publishing endpoint or platform write adapter.
- Enforce scopes, policy, budget reservation, storage quota, rights/consent, and disclosure before a creative external effect.
- Require configurable free-space capacity and a bounded temporary directory before media commands. Do not run global Docker cleanup.
- Use `ffmpeg`/`ffprobe` only through `MediaEngine` and C2PA only through `C2paTool`; unavailable status is explicit and testable.
- Record checkpoints, audit, provenance, trace IDs, capability/provider/model versions, and reverse lineage for every terminal result.

## File Structure

| Path | Responsibility |
| --- | --- |
| `src/salience/creative/contracts.py` | Frozen Phase 7–8 DTOs and lifecycle constants. |
| `src/salience/creative/capabilities.py` | Capability vocabulary, provider selection, and ceilings. |
| `src/salience/creative/repository.py` | Idempotent canonical writes and final-package reverse lineage. |
| `src/salience/creative/scripts.py` | Claim-preserving script revisions and deterministic checks. |
| `src/salience/creative/governance.py` | Rights, disclosure, platform, originality, and final decisions. |
| `src/salience/creative/providers.py` | Owned async provider contract, fixtures, and Synthesia REST adapter. |
| `src/salience/creative/media.py` | Storage guard, `ffprobe`, FFmpeg composition, and C2PA adapter. |
| `src/salience/creative/service.py` | Phase 8 assembly helpers consumed by activities. |
| `src/salience/agents/creative.py` | Writer, Creative Director, Production, and Verifier runtimes. |
| `src/salience/workflows/creative.py` | Temporal creative workflow and activities. |
| `migrations/versions/0006_creative_production_distribution.py` | Additive canonical Phase 7–8 schema. |
| `src/salience/api/routes/creative.py` | Scoped creative control surface without publishing. |

### Task 1: Environment Guard, Configuration, and Dependency Decision

**Files:**
- Create: `src/salience/creative/__init__.py`
- Create: `src/salience/creative/media.py`
- Modify: `src/salience/config.py`
- Modify: `tests/test_config.py`
- Create: `tests/unit/test_creative_media_environment.py`
- Modify: `docs/dependencies.md`
- Modify: `docs/implementation-progress.md`

**Interfaces:** Produces `CreativeRuntimeSettings`, `StorageCapacityGuard`, `MediaToolUnavailable`, and `MediaEngine.require_available()` for all media/provider work.

- [ ] **Step 1: Write failing tests**

```python
def test_creative_settings_rejects_non_positive_quotas(base_environment):
    with pytest.raises(ConfigurationError, match="CREATIVE_MAX_VARIANTS"):
        Settings.from_mapping(base_environment | {"CREATIVE_MAX_VARIANTS": "0"})

def test_media_engine_reports_missing_ffprobe(tmp_path):
    engine = MediaEngine(ffmpeg_path="missing", ffprobe_path="missing", temporary_root=tmp_path)
    with pytest.raises(MediaToolUnavailable, match="ffprobe"):
        engine.require_available("inspect")
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/test_config.py tests/unit/test_creative_media_environment.py -q`

Expected: imports fail because the creative runtime settings and `MediaEngine` do not exist.

- [ ] **Step 3: Implement the minimum safe configuration**

Add `creative_max_variants`, `creative_max_storage_bytes`, `creative_min_free_bytes`, `creative_provider_timeout_seconds`, `creative_temp_directory`, and `creative_synthesia_secret_ref` to `Settings`. Parse all quotas with `_positive_integer`; parse Synthesia only through `_optional_secret_reference`. Implement `StorageCapacityGuard.require_capacity(required_bytes)` using `shutil.disk_usage` and reject when `free - required_bytes < minimum_free_bytes`. `require_available()` only discovers a binary; it never installs one.

- [ ] **Step 4: Verify and checkpoint**

Run: `python -m compileall -q src && pytest tests/test_config.py tests/unit/test_creative_media_environment.py -q`

Expected: selected tests pass. Record `df -h .`, `docker system df`, FFmpeg/C2PA/Synthesia decisions, and no-install/no-cleanup state in `docs/dependencies.md` and progress; commit `feat: add creative runtime guards`.

### Task 2: Canonical Contracts and Capability Registry

**Files:**
- Create: `src/salience/creative/contracts.py`
- Create: `src/salience/creative/capabilities.py`
- Modify: `src/salience/contracts/plugins.py`
- Modify: `tests/contracts/test_plugin_registry.py`
- Create: `tests/unit/test_creative_contracts.py`

**Interfaces:** Produces frozen `ScriptDraftRequest`, `ScriptVersion`, `CreativeDirectionRequest`, `CreativePlan`, `CreativeCapabilityRequest`, `ProviderJobResult`, `AssetVariant`, `PlatformProfile`, `DisclosureDecision`, `ReadyToPublishPackage`, and `CreativeCapabilityRegistry.resolve(request) -> PluginManifest`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_script_request_requires_an_exact_brief_and_claim_links():
    with pytest.raises(ValidationError):
        ScriptDraftRequest(brief_id="", claim_ids=[])

def test_capability_resolution_rejects_an_unsupported_video_request():
    registry = CreativeCapabilityRegistry(PluginRegistry(supported_contract_version="1.0"))
    with pytest.raises(CapabilityNotSupported):
        registry.resolve(CreativeCapabilityRequest(capability="text_to_video", aspect_ratio="9:16"))
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/unit/test_creative_contracts.py tests/contracts/test_plugin_registry.py -q`

Expected: imports fail because the creative contract package does not exist.

- [ ] **Step 3: Define immutable provider-neutral contracts**

Add the complete creative capability vocabulary from the design. Require stable request/program/brief/script IDs, normalized input, references, expected modality, aspect ratio, resolution, duration, `max_variants`, provider extension, and non-secret secret scope. Validate creative plugin metadata keys `modalities`, `formats`, `async_support`, `polling_support`, `webhook_support`, `max_concurrency`, `estimated_cost_micros`, and `limitations`. Resolve only enabled manifests that satisfy capability/version/limits/effect constraints; explicit provider ID is a constraint, never a canonical type.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/unit/test_creative_contracts.py tests/contracts/test_plugin_registry.py -q`

Expected: pass with fixture and replacement-fixture `text_to_video` manifests. Commit `feat: define creative capability contracts`.

### Task 3: Additive Creative and Distribution Schema

**Files:**
- Create: `migrations/versions/0006_creative_production_distribution.py`
- Modify: `src/salience/db/models.py`
- Create: `tests/integration/test_creative_migrations.py`

**Interfaces:** Produces typed additive models for scripts, creative direction, provider jobs, assets, rights, provenance, platform profiles, distribution packages, and final packages.

- [ ] **Step 1: Write failing migration invariant tests**

```python
async def test_creative_schema_anchors_the_final_package(database_url):
    tables = await table_names(database_url)
    assert {"script_versions", "creative_jobs", "provider_jobs", "assets", "platform_profiles", "ready_to_publish_packages"} <= tables
    assert "uq_ready_package_program_key_version" in await unique_constraints(database_url, "ready_to_publish_packages")
```

- [ ] **Step 2: Run red test**

Run: `pytest tests/integration/test_creative_migrations.py -q`

Expected: revision `0006_creative_production_distribution` is absent.

- [ ] **Step 3: Implement additive schema and models**

Create `script_versions`, `creative_briefs`, `storyboards`, `shot_plans`, `creative_jobs`, `provider_jobs`, `assets`, `asset_variants`, `asset_relationships`, `caption_tracks`, `compositions`, `asset_licenses`, `consent_records`, `likeness_identities`, `voice_identities`, `usage_restrictions`, `asset_provenance`, `platform_profiles`, `distribution_packages`, `distribution_package_variants`, `title_thumbnail_candidates`, `localizations`, `originality_evaluations`, `synthetic_media_disclosures`, and `ready_to_publish_packages`. Use workspace/program UUID foreign keys, trace fields, version/status columns, and named uniqueness for script/version, request fingerprint, provider external ID, content hash/media type, profile/version, distribution/version, and ready-package/version. A final package references one brief, approved script, distribution package, and platform profile.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/integration/test_creative_migrations.py tests/integration/test_intelligence_migrations.py tests/integration/test_migrations.py -q`

Expected: pass through migration `0006`. Commit `feat: add creative production schema`.

### Task 4: Idempotent Repository, Asset Ownership, and Reverse Lineage

**Files:**
- Create: `src/salience/creative/repository.py`
- Create: `tests/integration/test_creative_repository.py`
- Modify: `src/salience/intelligence/repository.py`

**Interfaces:** Produces `CreativeRepository.record_script`, `record_creative_job`, `record_provider_job`, `record_asset_variant`, `select_asset`, `record_distribution_package`, `record_ready_package`, and `lineage_for_ready_package`.

- [ ] **Step 1: Write failing idempotency and lineage tests**

```python
async def test_provider_submission_and_asset_import_are_idempotent(repository, creative_input):
    assert await repository.record_provider_job(**creative_input) == await repository.record_provider_job(**creative_input)

async def test_ready_package_reverse_lineage_reaches_source_evidence(repository, seeded_brief):
    ready_id = await build_ready_package(repository, seeded_brief)
    lineage = await repository.lineage_for_ready_package(ready_id)
    assert lineage["brief_id"] == seeded_brief.id
    assert lineage["source_ids"] and lineage["agent_run_ids"] and lineage["asset_ids"]
```

- [ ] **Step 2: Run red test**

Run: `pytest tests/integration/test_creative_repository.py -q`

Expected: `CreativeRepository` import fails.

- [ ] **Step 3: Implement parameterized idempotent persistence**

Use parameterized Psycopg `INSERT` statements with an explicit conflict target and `RETURNING id::text` for every write. Reuse same-program asset bytes by content hash/media type; retain selected/rejected reason and parent relationships. `lineage_for_ready_package` must join final package through distribution/assets/variants/provider job/creative job/storyboard/script/brief to package/opportunity/signals/fetches/evidence/sources and agent/model/tool IDs.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/integration/test_creative_repository.py tests/integration/test_content_brief_repository.py tests/integration/test_intelligence_repository.py -q`

Expected: pass. Commit `feat: persist creative lineage`.

### Task 5: Script, Rights, Disclosure, Platform, and Originality Gates

**Files:**
- Create: `src/salience/creative/scripts.py`
- Create: `src/salience/creative/governance.py`
- Create: `tests/unit/test_creative_scripts.py`
- Create: `tests/unit/test_creative_governance.py`
- Create: `tests/evals/test_phase7_script_eval.py`
- Create: `tests/evals/test_phase8_distribution_eval.py`

**Interfaces:** Produces `ScriptVerifier.evaluate(script, brief)`, `RightsPolicy.authorize(request, records)`, `DisclosurePolicy.decide(input)`, `PlatformValidator.validate(package, profile)`, and `OriginalityValidator.evaluate(candidate, history)`.

- [ ] **Step 1: Write failing safety/evaluation tests**

```python
def test_script_rejects_claim_absent_from_brief_claim_ids(approved_brief):
    result = ScriptVerifier().evaluate(script_with_claim("new unsupported statistic"), approved_brief)
    assert "unsupported_claim" in result.blocker_codes

def test_rights_policy_fails_closed_for_missing_likeness_consent():
    decision = RightsPolicy().authorize(uses_real_likeness=True, consent=None)
    assert decision.allowed is False
    assert decision.reason == "missing_consent"

def test_platform_profile_blocks_caption_and_aspect_ratio_violation(profile):
    result = PlatformValidator().validate(package_with_caption("x" * 101), profile)
    assert {"caption_limit", "aspect_ratio"} <= set(result.blocker_codes)
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/unit/test_creative_scripts.py tests/unit/test_creative_governance.py tests/evals/test_phase7_script_eval.py tests/evals/test_phase8_distribution_eval.py -q`

Expected: imports fail because script and governance modules do not exist.

- [ ] **Step 3: Implement deterministic gates**

Script checks verify brief ID, claim/evidence subset, hook/promise/audience consistency, duration/section bounds, repeated text, CTA policy, misleading framing, and contradiction markers. Rights checks enforce active/non-revoked consent, identity, permitted channel, territory, commercial permission, and expiry. Disclosure uses generated/altered/realistic/real-person/voice-clone/C2PA/profile/jurisdiction state. Originality compares normalized title, narrative fingerprint, thumbnail fingerprint, template key, and derivative distance. Platform checks use the supplied versioned profile only.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/unit/test_creative_scripts.py tests/unit/test_creative_governance.py tests/evals/test_phase7_script_eval.py tests/evals/test_phase8_distribution_eval.py -q`

Expected: pass for contradictory, missing/revoked consent, clickbait, duplicate, localization-claim, and disclosure cases. Commit `feat: govern scripts and distribution packages`.

### Task 6: Callable Creative Specialist Agents

**Files:**
- Create: `src/salience/agents/creative.py`
- Modify: `src/salience/agents/specialists.py`
- Modify: `src/salience/agents/fixtures.py`
- Create: `tests/unit/test_creative_agents.py`
- Modify: `tests/integration/test_direct_delegated_intelligence_agents.py`

**Interfaces:** Produces `writer_agent`, `creative_director_agent`, `production_agent`, and `verifier_agent` manifests/runtimes consuming `AgentInvocation` and `AgentExecutionContext`.

- [ ] **Step 1: Write failing direct/delegated tests**

```python
async def test_writer_direct_and_delegated_outputs_use_the_same_script_contract(service, brief_input):
    direct = await service.invoke_by_id("writer_agent", brief_input)
    delegated = await service.invoke_from_parent("lead_content_agent", AgentInvocation("writer_agent", brief_input))
    assert direct.output["contract_version"] == delegated.output["contract_version"] == "ScriptVersion@v1"

async def test_production_agent_caps_requested_variants(service, production_input):
    output = (await service.invoke_by_id("production_agent", production_input | {"max_variants": 4})).output
    assert output["requested_variants"] == 3
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/unit/test_creative_agents.py tests/integration/test_direct_delegated_intelligence_agents.py -q`

Expected: registry lookup fails for each new agent.

- [ ] **Step 3: Register native runtimes**

Writer emits structured sections with brief claim/evidence IDs. Creative Director emits provider-neutral shots and capability requests. Production emits bounded planned requests/selection criteria and delegates external work to workflow activities. Verifier emits semantic advisory findings only. All manifests have explicit scopes, sync/async support, and no publishing authority; delegated authority continues to intersect parent scopes through `AgentService`.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/unit/test_creative_agents.py tests/integration/test_direct_delegated_intelligence_agents.py tests/unit/test_agent_execution.py -q`

Expected: pass with schema validation and no authority escalation. Commit `feat: add callable creative agents`.

### Task 7: Async Provider Contract, Fixtures, and Synthesia Adapter

**Files:**
- Create: `src/salience/creative/providers.py`
- Create: `tests/unit/test_creative_providers.py`
- Create: `tests/integration/test_synthesia_provider_contract.py`
- Modify: `src/salience/creative/capabilities.py`

**Interfaces:** Produces `CreativeProvider.submit`, `get_status`, `cancel`, `verify_webhook`, `download`; `FixtureCreativeProvider`, `ReplacementFixtureCreativeProvider`, and `SynthesiaCreativeProvider`.

- [ ] **Step 1: Write failing reconciliation tests**

```python
async def test_fixture_provider_returns_one_external_id_per_idempotency_key(provider, request):
    assert (await provider.submit(request)).external_job_id == (await provider.submit(request)).external_job_id

async def test_synthesia_adapter_maps_submit_without_leaking_authorization(httpx_mock, request):
    result = await SynthesiaCreativeProvider(client=httpx_mock, secret_resolver=resolver).submit(request)
    assert result.state == "submitted"
    assert "secret" not in repr(result).lower()
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/unit/test_creative_providers.py tests/integration/test_synthesia_provider_contract.py -q`

Expected: provider imports fail.

- [ ] **Step 3: Implement owned provider lifecycle**

Fixture providers expose `submitted`, `running`, `completed`, `failed`, and `cancelled`, including duplicate webhooks and poll states. Synthesia maps `POST https://api.synthesia.io/v2/videos` through injected `httpx`, captures stable callback/idempotency mapping and rate-limit reset metadata, and returns only owned DTOs. Map malformed/403/429/4xx/5xx states to owned failure classes. Reject unsigned webhook input unless an injected configured verifier accepts it; no live network call occurs without explicit secret resolution.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/unit/test_creative_providers.py tests/integration/test_synthesia_provider_contract.py -q`

Expected: pass for submit/poll/cancel, duplicate webhook, webhook/poll ordering, timeout after acceptance, malformed response, provider rejection, and replacement capability parity. Commit `feat: add reconciled creative providers`.

### Task 8: Owned Media, FFmpeg, Captions, and C2PA Boundaries

**Files:**
- Modify: `src/salience/creative/media.py`
- Create: `tests/unit/test_creative_media.py`
- Create: `tests/integration/test_creative_object_storage.py`
- Modify: `tests/contracts/test_object_store.py`

**Interfaces:** Produces `MediaEngine.inspect`, `compose`, `store_download`, `render_caption_track`, `C2paTool.inspect`, and safe `MediaInspection` unavailable receipts.

- [ ] **Step 1: Write failing media/ownership tests**

```python
async def test_store_download_reuses_owned_bytes_by_sha256(memory_store, tmp_path):
    engine = MediaEngine(
        object_store=memory_store,
        capacity_guard=StorageCapacityGuard(minimum_free_bytes=0),
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="missing-ffprobe",
        temporary_root=tmp_path,
    )
    receipt = await engine.store_download(b"fixture-media", media_type="video/mp4")
    assert receipt.content_hash == (await engine.store_download(b"fixture-media", media_type="video/mp4")).content_hash

def test_inspection_marks_missing_ffprobe_not_run(tmp_path):
    engine = MediaEngine(
        object_store=MemoryObjectStore(),
        capacity_guard=StorageCapacityGuard(minimum_free_bytes=0),
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="missing-ffprobe",
        temporary_root=tmp_path,
    )
    result = engine.inspect(tmp_path / "asset.mp4")
    assert (result.status, result.reason) == ("not_run", "ffprobe_unavailable")
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/unit/test_creative_media.py tests/integration/test_creative_object_storage.py -q`

Expected: required media methods and DTO fields are missing.

- [ ] **Step 3: Implement bounded media behavior**

Stream a download into one guarded temporary file, hash it while writing, enforce quota, validate expected MIME, persist through `ObjectStore`, then delete only that verified temporary file. Invoke `ffprobe -v error -show_format -show_streams -of json` without a shell and classify codec/container/dimensions/duration/frame rate/audio/sample rate/decode/size failures. Compose only known local paths with argv/time/output bounds. `C2paTool.inspect` returns `not_configured`, `unavailable`, `invalid`, `valid_untrusted`, or `valid_trusted`, and never writes a manifest or loads a signing key.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/unit/test_creative_media.py tests/integration/test_creative_object_storage.py tests/contracts/test_object_store.py -q`

Expected: fixture contracts pass. A real FFmpeg test reports `NOT RUN: ffprobe_unavailable` if the binary is still absent; that result is never treated as a media-engine pass. Commit `feat: add governed media artifact pipeline`.

### Task 9: Durable Creative Workflow and Restart-Safe Reconciliation

**Files:**
- Create: `src/salience/workflows/creative.py`
- Modify: `src/salience/workflows/worker.py`
- Modify: `src/salience/workflows/persistence.py`
- Create: `tests/e2e/test_phase7_creative_recovery.py`
- Create: `tests/unit/test_creative_workflow.py`

**Interfaces:** Produces `CreativeProductionRequest`, `CreativeProductionResult`, `CreativeWorkflowState`, `CreativeActivities`, `CreativeProductionWorkflow`, and `build_creative_worker`.

- [ ] **Step 1: Write failing restart verifier**

```python
@pytest.mark.asyncio
async def test_restart_after_provider_acceptance_reconciles_without_resubmission(temporal_client, seeded_brief):
    result = await run_creative_restart_scenario(temporal_client, brief_id=seeded_brief.id, crash_at="provider.submitted")
    assert result.provider_submit_count == 1
    assert result.ready_package_id
    assert "provider.reconciled" in result.checkpoints
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/unit/test_creative_workflow.py tests/e2e/test_phase7_creative_recovery.py -q`

Expected: creative workflow import fails.

- [ ] **Step 3: Implement workflow and activities**

Implement activities named `creative.load_brief`, `creative.script`, `creative.verify_script`, `creative.direction`, `creative.authorize`, `creative.submit_or_reconcile`, `creative.await_provider`, `creative.import_validate`, `creative.select`, `creative.compose`, `creative.distribute`, `creative.final_gate`, `creative.complete`, and `creative.cancel`. Each writes a canonical checkpoint. Retries apply only to safe pre-submission/read activities; `submit_or_reconcile` persists/inspects the provider job before any retry. Register the new workflow/activities without changing existing registrations.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/unit/test_creative_workflow.py tests/e2e/test_phase7_creative_recovery.py tests/e2e/test_phase5_durable_research.py -q`

Expected: pass, including cancellation, budget-reservation excess, provider failure, and duplicate webhook/poll cases. Commit `feat: add durable creative production workflow`.

### Task 10: Phase 8 Distribution Assembly and Immutable Final Package

**Files:**
- Create: `src/salience/creative/service.py`
- Create: `tests/integration/test_distribution_package_repository.py`
- Modify: `src/salience/creative/repository.py`
- Modify: `src/salience/creative/governance.py`
- Create: `tests/evals/test_phase8_ready_package_eval.py`

**Interfaces:** Produces `CreativeService.build_distribution`, `CreativeService.finalize_ready_package`, and versioned distribution/final DTOs.

- [ ] **Step 1: Write failing finalization tests**

```python
async def test_finalization_emits_immutable_ready_package(service, approved_production):
    ready = await service.finalize_ready_package(approved_production)
    assert ready.contract_version == "ReadyToPublishPackage@v1"
    assert ready.approval_state == "approved"
    assert ready.disclosure_decision_id and ready.platform_profile_id

async def test_finalization_refuses_missing_consent(service, production_without_consent):
    with pytest.raises(CreativeGovernanceDenied, match="missing_consent"):
        await service.finalize_ready_package(production_without_consent)
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/integration/test_distribution_package_repository.py tests/evals/test_phase8_ready_package_eval.py -q`

Expected: finalization service is absent.

- [ ] **Step 3: Implement Phase 8 assembly**

Generate bounded title/thumbnail candidates and retain selection/rejection reasons. Persist localized derivatives with source/target locale, terminology constraints, adapted CTA, subtitle/audio reference, and original verified claims. Build distribution only after platform/originality/disclosure checks. Finalization writes an immutable ready-package version and never includes publication account IDs or API payloads.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/integration/test_distribution_package_repository.py tests/evals/test_phase8_distribution_eval.py tests/evals/test_phase8_ready_package_eval.py -q`

Expected: pass for profile violations, misleading title/thumbnail, duplicate candidate, localization claim injection, disclosure, and immutability. Commit `feat: assemble governed distribution packages`.

### Task 11: Scoped Control API, CLI, SDK, and Complete E2E Verifier

**Files:**
- Create: `src/salience/api/routes/creative.py`
- Modify: `src/salience/api/app.py`
- Modify: `src/salience/api/schemas.py`
- Modify: `src/salience/api/dependencies.py`
- Modify: `src/salience/cli.py`
- Modify: `src/salience/sdk/client.py`
- Create: `tests/integration/test_creative_control_api.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `tests/unit/test_sdk.py`
- Create: `tests/e2e/test_phases_7_8_creative_loop.py`

**Interfaces:** Produces start/inspect/script/asset/package/lineage read routes and matching CLI/SDK methods; there is no publish command or endpoint.

- [ ] **Step 1: Write failing public-surface/E2E tests**

```python
async def test_creative_start_defaults_to_dry_run_and_returns_job_trace(api_client, seeded_brief):
    response = await api_client.post("/v1/creative/runs", json={"brief_id": seeded_brief.id, "idempotency_key": "creative-api-1"})
    assert response.status_code == 202
    assert response.json()["dry_run"] is True

@pytest.mark.asyncio
async def test_brief_to_ready_package_has_complete_reverse_lineage(temporal_client, seeded_brief):
    result = await run_complete_creative_fixture_loop(temporal_client, seeded_brief.id)
    assert result.ready_package_id
    assert (await creative_repository.lineage_for_ready_package(result.ready_package_id))["source_ids"]
```

- [ ] **Step 2: Run red tests**

Run: `pytest tests/integration/test_creative_control_api.py tests/unit/test_cli.py tests/unit/test_sdk.py tests/e2e/test_phases_7_8_creative_loop.py -q`

Expected: routes/CLI/SDK import or endpoint assertions fail.

- [ ] **Step 3: Implement public surfaces**

Start requests require brief/program IDs, idempotency key, selected profile keys, and default `dry_run=True`. Inspection returns IDs, state, trace, and safe metadata only. Add `content creative start`, `content creative inspect`, `content creative package-lineage`, and matching `SalienceClient` methods using the existing HTTP-only pattern. Require `control:write` to start and `control:read` to inspect.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/integration/test_creative_control_api.py tests/unit/test_cli.py tests/unit/test_sdk.py tests/e2e/test_phases_7_8_creative_loop.py tests/e2e/test_phase7_creative_recovery.py -q`

Expected: pass and prove the complete fixture brief-to-ready-package path and reverse lineage. Commit `feat: expose creative control surface`.

### Task 12: Documentation, Visuals, Full Verification, and Phase-9 Handoff

**Files:**
- Modify: `README.md`, `docs/architecture.md`, `docs/database.md`, `docs/api.md`, `docs/agents.md`, `docs/plugins.md`, `docs/governance.md`, `docs/dependencies.md`, `docs/deployment.md`, `docs/verification.md`, `docs/limitations.md`, `docs/phase-7-handoff.md`, `docs/salience-phase-1-6.architecture.json`, and `docs/implementation-progress.md`
- Create: `docs/phase-9-handoff.md`, `scripts/verify-phases-7-8.sh`, `tests/scripts/test_phase_7_8_documentation.sh`

**Interfaces:** Produces a project-owned verifier reporting `PASS`, `FAIL`, or `NOT RUN` with an exact reason and names `ReadyToPublishPackage@v1` as the only Phase-9 publishing input.

- [ ] **Step 1: Write failing documentation/verifier tests**

```bash
grep -F 'ReadyToPublishPackage@v1' docs/phase-9-handoff.md
grep -F 'bash scripts/verify-phases-7-8.sh' README.md docs/verification.md
grep -F 'NOT RUN' scripts/verify-phases-7-8.sh
```

- [ ] **Step 2: Run the red documentation test**

Run: `bash tests/scripts/test_phase_7_8_documentation.sh`

Expected: missing Phase-9 handoff and verifier references.

- [ ] **Step 3: Implement docs and verifier**

Make the verifier check venv/services, migration, Task 11 suite, FFmpeg/C2PA availability, and clear optional-tool `NOT RUN` output. Document provider/secret references, fixture behavior, storage measurements, rights/consent, no-publish boundary, unavailable live tests, and exact reproduction commands. Update and deliver the Archify source/HTML, run showcase validation and Chromium visual checks, and perform image-capable spot review.

- [ ] **Step 4: Run complete verification**

```bash
bash tests/scripts/test_phase_7_8_documentation.sh
bash scripts/verify-phases-7-8.sh
pytest tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q
python -m compileall -q src
git diff --check
node .agents/skills/archify/bin/archify.mjs validate architecture docs/salience-phase-1-6.architecture.json --quality showcase --repo-root . --json
```

Expected: fixture/governance/E2E tests pass. Any unavailable FFmpeg, C2PA signing, or live-provider check reports `NOT RUN` with its actual prerequisite. Record full result, warnings, duration, storage measurement, and omitted tests verbatim in progress/verification docs.

- [ ] **Step 5: Inspect, repair, re-verify, and checkpoint**

Use `superpowers:systematic-debugging` for each unexpected failure; re-run the failing focused test before the complete block above. Update progress after every verified repair, then commit `docs: complete phases 7-8 production runbooks`.

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 deliver environment/capability boundaries; Tasks 3–4 add canonical identities and reverse lineage; Task 5 enforces script/right/disclosure/platform/originality rules; Task 6 preserves callable agents; Tasks 7–9 deliver providers/media/C2PA/restart safety; Tasks 10–11 deliver distribution/final-package/API/CLI/SDK/E2E; Task 12 supplies documentation, visuals, verifier, regression, and Phase-9 handoff.
- **No-placeholder scan:** Each task has exact paths, interfaces, red tests, test commands, expected outcome, implementation requirements, and commit checkpoint.
- **Type consistency:** `CreativeCapabilityRequest`, `ProviderJobResult`, `CreativeProductionRequest`, `CreativeProductionResult`, `CreativeRepository`, `MediaEngine`, `C2paTool`, and `ReadyToPublishPackage` are defined before later tasks consume them.

## Execution Handoff

The plan is `docs/superpowers/plans/2026-09-13-creative-production-phases-7-8.md`.

The active collaboration policy allows inline execution only unless the user explicitly requests delegated agents. Invoke `superpowers:executing-plans` to execute this plan task-by-task after its review.
