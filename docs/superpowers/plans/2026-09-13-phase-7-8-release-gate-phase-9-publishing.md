# Phase 7–8 Release Gate and Phase 9 Governed Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the creative `ReadyToPublishPackage@v1` path release-ready, then build one governed, durable, duplicate-safe publishing path to a canonical `Publication` without mutating the creative package.

**Architecture:** Gate One extends the existing creative repository, workflow, provider contract, and PostgreSQL schema with durable cost settlement, lifecycle/webhook state, registry selection, rights/provenance, and database-enforced decision immutability. Gate Two introduces a separate `salience.publication` domain whose fixture and optional YouTube adapter use the same owned external-effect, policy, cost, scheduling, audit, provenance, and trace boundaries.

**Tech Stack:** Python 3.13, PostgreSQL 17, Psycopg 3.3, SQLAlchemy/Alembic, Temporal 1.32, FastAPI 0.141, HTTPX 0.28, Pydantic 2.12, existing object-storage contract, pytest; no Google SDK or custom OAuth implementation.

**Spec:** `docs/superpowers/specs/2026-09-13-phase-7-8-release-gate-phase-9-publishing-design.md`

## Global Constraints

- Do not start Tasks 12–17 until Task 11's Phase 7–8 release barrier is green and the independent review records no critical/high finding.
- Never store credential values, OAuth tokens, refresh tokens, client secrets, signed delivery URLs, or provider SDK/wire objects in canonical records, logs, checkpoints, audit, provenance, or API responses.
- Preserve every existing Phase 1–8 identity and historical record. New migrations are additive, and only corrective database triggers restrict mutation of already approved decision lineage.
- Use fixture providers for normal CI. A real adapter is opt-in and returns `NOT RUN: <reason>` unless all secure configuration exists.
- Use PostgreSQL transactions and row locking for reservation/settlement and conditional lifecycle transitions; do not restore an in-memory budget authority.
- Keep provider and publisher adapters versioned and capability-driven. The runtime, not an agent, enforces authorization, idempotency, credential resolution, costs, and external writes.
- Reuse active PostgreSQL and Temporal services for focused tests. Do not perform global Docker cleanup or add image builds while the host has constrained free space.
- Commit every completed task and append its verifier result, remaining risk, and next action to `docs/implementation-progress.md`.

## File Structure

| Path | Responsibility |
| --- | --- |
| `migrations/versions/0008_creative_release_gate.py` | Add creative reservation, provider-receipt, rights-link, and immutable-decision database invariants. |
| `src/salience/governance/cost_repository.py` | Transactional canonical reservation, pending-actual, settlement, release, and overage lifecycle. |
| `src/salience/creative/contracts.py` | Extended bounded capability, rights, variant, lifecycle, and webhook DTOs. |
| `src/salience/creative/capabilities.py` | Candidate filtering and deterministic provider selection. |
| `src/salience/creative/repository.py` | Creative lifecycle, receipt, rights/provenance, immutable-version writers, and lineage readers. |
| `src/salience/creative/providers.py` | Capability metadata, typed failures, reconciliation, cancel, webhook verification, and usage DTO mapping. |
| `src/salience/workflows/creative.py` | Durable reserve/submit/await/cancel/settle activities with bounded polling. |
| `src/salience/api/routes/creative.py` | Signed creative webhook ingress and lifecycle inspection only. |
| `src/salience/publication/*` | Phase-9 contracts, credential boundary, registry, adapters, policy, repository, delivery, and workflow helpers. |
| `migrations/versions/0009_governed_publication.py` | Canonical account, capability, request, plan, attempt, receipt, schedule, and publication records. |
| `src/salience/workflows/publication.py` | Temporal publication lifecycle and schedule execution. |
| `src/salience/api/routes/publication.py` | Scoped publication request, schedule, inspection, cancellation, and webhook control surface. |
| `tests/**/test_phase7_8_release_gate*.py` | Gate-One repository, lifecycle, crash/retry, rights, and immutability contracts. |
| `tests/**/test_phase9*.py` | Fixture-first publisher, control API, recovery, receipt, and policy contracts. |

---

## Gate One — Phase 7–8 Release Repairs

### Task 1: Specify the release-gate lifecycle contracts and red tests

**Files:**
- Modify: `src/salience/creative/contracts.py`
- Modify: `src/salience/creative/providers.py`
- Modify: `src/salience/creative/capabilities.py`
- Test: `tests/unit/test_creative_contracts.py`
- Test: `tests/unit/test_creative_providers.py`
- Test: `tests/contracts/test_plugin_registry.py`

**Interfaces:** Produces immutable `CreativeRightsContext`, `CreativeVariantPlan`, `ProviderUsage`, `ProviderWebhookEvent`, and lifecycle states `planned`, `submitting`, `submitted`, `running`, `completed`, `failed`, `cancelled`, and `dead_lettered`. `CreativeProvider` exposes `capabilities`, `submit`, `reconcile`, `get_status`, `cancel`, `verify_webhook`, and `download` using owned DTOs.

- [x] **Step 1: Write failing contract tests**

```python
def test_capability_request_rejects_a_variant_count_above_configured_quota():
    with pytest.raises(ValidationError, match="max_variants"):
        CreativeCapabilityRequest(**request_data, max_variants=4)

def test_provider_result_preserves_explicit_unknown_actual_cost():
    result = ProviderJobResult(**result_data, usage=ProviderUsage(actual_micros=None))
    assert result.usage.actual_micros is None
    assert result.usage.actual_cost_status == "pending"

async def test_fixture_webhook_has_a_stable_delivery_identity_and_no_secret_fields(provider):
    event = await provider.verify_webhook({"id": "fixture-job", "status": "completed", "delivery_id": "d1"}, signature="fixture-signature")
    assert event.delivery_id == "d1"
    assert "secret" not in event.model_dump_json().casefold()
```

- [x] **Step 2: Run the contract tests and confirm they fail**

Run: `pytest tests/unit/test_creative_contracts.py tests/unit/test_creative_providers.py tests/contracts/test_plugin_registry.py -q`

Expected: FAIL because the new rights, usage, webhook, and capability metadata contracts do not exist.

- [x] **Step 3: Add the minimal versioned DTOs and metadata filtering inputs**

```python
class ProviderUsage(BaseModel):
    estimated_micros: int = Field(ge=0)
    actual_micros: int | None = Field(default=None, ge=0)
    actual_cost_status: Literal["pending", "known"] = "pending"

class ProviderWebhookEvent(BaseModel):
    provider_id: str
    delivery_id: str | None
    external_job_id: str
    state: ProviderLifecycleState
    safe_payload_hash: str
```

Require plugin metadata to declare supported capability, modality, formats, aspect ratios, duration range, polling/webhook/cancel support, enabled state, contract compatibility, concurrency, and rate state. Validate every declared lifecycle/usage value before repository use.

- [x] **Step 4: Verify and checkpoint**

Run: `pytest tests/unit/test_creative_contracts.py tests/unit/test_creative_providers.py tests/contracts/test_plugin_registry.py -q && python -m compileall -q src && git diff --check`

Expected: PASS. Commit: `feat: define creative release gate contracts`.

### Task 2: Add additive Phase 7–8 release-gate migration and model metadata

**Files:**
- Create: `migrations/versions/0008_creative_release_gate.py`
- Modify: `src/salience/db/models.py`
- Test: `tests/integration/test_creative_release_gate_migration.py`
- Test: `tests/unit/test_creative_persistence_models.py`

**Interfaces:** Adds `creative_job_effects`, `creative_provider_webhook_receipts`, `creative_job_rights`, and `asset_rights_links`; extends provider/job records with terminal timestamps and reservation/actual usage state; adds PostgreSQL triggers that reject mutation/deletion of decisions referenced by `ready_to_publish_packages`.

- [x] **Step 1: Write migration and direct-SQL red tests**

```python
def test_release_gate_migration_has_canonical_effect_and_webhook_uniqueness(connection):
    assert _unique_columns(connection, "creative_provider_webhook_receipts") == {
        ("provider_id", "delivery_identity"),
        ("provider_job_id", "safe_payload_hash"),
    }

def test_ready_package_referenced_disclosure_rejects_direct_update(connection, ready_package):
    with pytest.raises(psycopg.errors.RaiseException, match="immutable approved distribution decision"):
        connection.execute("UPDATE synthetic_media_disclosures SET status = 'rejected' WHERE id = %s", (ready_package.disclosure_id,))
```

- [x] **Step 2: Run the focused migration suite and confirm it fails**

Run: `pytest tests/integration/test_creative_release_gate_migration.py tests/unit/test_creative_persistence_models.py -q`

Expected: FAIL because revision `0008` and release-gate objects are absent.

- [x] **Step 3: Implement migration `0008_creative_release_gate`**

Use additive `CREATE TABLE`/`ALTER TABLE` statements only. Link a creative job to one canonical external effect and reservation. Store webhook raw-safe hash, verified signature state, provider delivery identity, state, received time, trace/span, and provenance references. Add rights-link tables that reference canonical license/consent/likeness/voice/restriction/asset IDs without JSON-only foreign keys. Add immutable-decision trigger functions for distribution package, title-thumbnail candidate, localization, originality evaluation, disclosure, and approval fields once a ready package reaches approved state.

- [x] **Step 4: Verify migration forwards and backwards**

Run: `alembic upgrade head && pytest tests/integration/test_creative_release_gate_migration.py tests/unit/test_creative_persistence_models.py -q`

Expected: PASS, including direct SQL immutability rejection. Commit: `feat: persist creative release gate state`.

### Task 3: Implement durable canonical budget reservation and settlement

**Files:**
- Create: `src/salience/governance/cost_repository.py`
- Modify: `src/salience/governance/costs.py`
- Modify: `src/salience/workflows/persistence.py`
- Test: `tests/integration/test_creative_cost_lifecycle.py`
- Test: `tests/unit/test_costs.py`

**Interfaces:** `CostReservationRepository.reserve_for_effect(...)`, `record_actual_usage(...)`, `settle(...)`, and `release_unused(...)` return typed records. Reservation identity is `(budget_id, reservation_key)` and links one job/effect/creative job atomically.

- [ ] **Step 1: Write transactional red tests**

```python
async def test_reserve_for_effect_is_idempotent_after_restart(repository, creative_effect):
    first = await repository.reserve_for_effect(**creative_effect, estimated_micros=100)
    second = await repository.reserve_for_effect(**creative_effect, estimated_micros=100)
    assert second.reservation_id == first.reservation_id
    assert await repository.reservation_count(effect_id=creative_effect["external_effect_id"]) == 1

async def test_settlement_records_overage_once_and_blocks_finalization(repository, reservation):
    outcome = await repository.settle(reservation.reservation_id, actual_micros=120)
    assert outcome.status == "overage_pending_approval"
    assert await repository.settle(reservation.reservation_id, actual_micros=120) == outcome
```

- [ ] **Step 2: Run the cost suite and confirm it fails**

Run: `pytest tests/integration/test_creative_cost_lifecycle.py tests/unit/test_costs.py -q`

Expected: FAIL because no PostgreSQL cost lifecycle repository exists.

- [ ] **Step 3: Implement lock-safe financial writes**

```python
def _reserve_for_effect(cursor, budget_id, reservation_key, estimated_micros, job_id, effect_id, creative_job_id):
    cursor.execute("SELECT id FROM budgets WHERE id = %s AND status = 'active' FOR UPDATE", (budget_id,))
    cursor.execute("SELECT COALESCE(SUM(reserved_amount), 0) FROM budget_reservations WHERE budget_id = %s AND status IN ('reserved', 'pending_actual') FOR UPDATE", (budget_id,))
    # Insert or return the matching immutable reservation; reject a changed estimate.
```

Record estimated, reserved, pending actual, actual, release, and overage values in integer micro-units. Use one database transaction for budget lock, availability check, reservation insert/reuse, creative-job link, external-effect link, audit, and provenance. Do not turn an unknown actual value into zero.

- [ ] **Step 4: Verify concurrency and restart properties**

Run: `pytest tests/integration/test_creative_cost_lifecycle.py tests/unit/test_costs.py tests/e2e/test_phase7_creative_recovery.py -q`

Expected: PASS for budget denial before submit, duplicate reserve/settle, pending actual, release, overage, and restart reuse. Commit: `feat: settle creative costs durably`.

### Task 4: Integrate durable creative authorization, reservation, and settlement

**Files:**
- Modify: `src/salience/workflows/creative.py`
- Modify: `src/salience/workflows/persistence.py`
- Modify: `src/salience/creative/repository.py`
- Test: `tests/unit/test_creative_workflow.py`
- Test: `tests/e2e/test_phase7_creative_recovery.py`

**Interfaces:** `CreativeActivities.authorize_and_reserve`, `submit_or_reconcile`, `await_provider`, and `settle_cost` persist reservation/effect/provider identities in workflow payloads and checkpoints.

- [ ] **Step 1: Write activity-level red tests**

```python
async def test_provider_submit_is_not_called_when_durable_reservation_is_denied(state):
    result = await CreativeActivities(state).authorize_and_reserve(seed_payload)
    assert result["authorized"] is False
    assert state.provider.submit_count == 0

async def test_restart_after_submit_reuses_reservation_and_settles_once(restarted_workflow):
    assert restarted_workflow.reservation_ids == [restarted_workflow.reservation_ids[0]]
    assert restarted_workflow.actual_ledger_entries == 1
```

- [ ] **Step 2: Run the focused workflow tests and confirm they fail**

Run: `pytest tests/unit/test_creative_workflow.py tests/e2e/test_phase7_creative_recovery.py -q`

Expected: FAIL because authorization still uses `budget_available_micros`.

- [ ] **Step 3: Replace the in-memory authority**

Remove `budget_available_micros` as a workflow authorization input. Plan an external effect before reserve, derive provider estimate from the selected provider capability/plan, reserve durably, and checkpoint reservation identity before submission. Persist actual usage from provider results; settle/release before distribution/finalization. Route overage and unresolved actual usage to a fail-closed terminal outcome with audit/provenance/dead-letter evidence.

- [ ] **Step 4: Verify all crash seams**

Run: `pytest tests/e2e/test_phase7_creative_recovery.py tests/e2e/test_phases_7_8_creative_loop.py tests/integration/test_creative_cost_lifecycle.py -q`

Expected: PASS for interruption before reserve, after reserve, after acceptance, after usage receipt, and after settlement. Commit: `feat: enforce creative cost lifecycle`.

### Task 5: Persist complete provider lifecycle, polling, dead letters, and cancellation

**Files:**
- Modify: `src/salience/creative/providers.py`
- Modify: `src/salience/creative/repository.py`
- Modify: `src/salience/workflows/creative.py`
- Modify: `src/salience/config.py`
- Test: `tests/integration/test_creative_provider_lifecycle.py`
- Test: `tests/unit/test_creative_providers.py`
- Test: `tests/e2e/test_phase7_creative_recovery.py`

**Interfaces:** `CreativeRepository.transition_provider_job(...)` applies allowed conditional state transitions. `CreativeActivities.await_provider` obeys `creative_provider_timeout_seconds`, bounded polling, provider error classification, and dead-letter behavior.

- [ ] **Step 1: Write lifecycle red tests**

```python
async def test_timeout_after_provider_acceptance_reconciles_without_a_second_submit(workflow):
    result = await workflow.run_with_provider_timeout()
    assert result.provider_submit_count == 1
    assert result.provider_state == "dead_lettered"

async def test_cancellation_reconciles_before_supported_provider_cancel(provider, repository):
    outcome = await cancel_creative_job(provider, repository, external_job_id="provider-1")
    assert outcome.state == "cancelled"
    assert provider.cancel_calls == ["provider-1"]
```

- [ ] **Step 2: Run the lifecycle suite and confirm it fails**

Run: `pytest tests/integration/test_creative_provider_lifecycle.py tests/unit/test_creative_providers.py tests/e2e/test_phase7_creative_recovery.py -q`

Expected: FAIL because lifecycle persistence, configured timeout, and cancellation are incomplete.

- [ ] **Step 3: Implement transition and cancellation rules**

Persist submitted/running/completed/failed/cancelled/dead-lettered states, external IDs, classified failure, retry-after, timestamps, and usage. Poll only until configured timeout. Terminal failures call `CanonicalJobStore.dead_letter` exactly once. Cancellation reads canonical state, reconciles accepted work, calls adapter cancel only when the selected manifest advertises support, persists result, then releases/settles the reservation according to known usage.

- [ ] **Step 4: Verify recovery and terminal paths**

Run: `pytest tests/integration/test_creative_provider_lifecycle.py tests/unit/test_creative_providers.py tests/e2e/test_phase7_creative_recovery.py -q`

Expected: PASS. Commit: `feat: complete creative provider lifecycle`.

### Task 6: Add signed creative webhook ingestion and poll/webhook convergence

**Files:**
- Modify: `src/salience/api/routes/creative.py`
- Modify: `src/salience/api/schemas.py`
- Modify: `src/salience/creative/repository.py`
- Modify: `src/salience/creative/providers.py`
- Test: `tests/integration/test_creative_webhook_ingress.py`
- Test: `tests/integration/test_creative_control_api.py`

**Interfaces:** `POST /v1/creative/providers/{provider_id}/webhooks` accepts raw-safe payload bytes and signature headers, delegates verification to the selected provider, and returns a receipt identity without exposing the payload.

- [ ] **Step 1: Write duplicate/race red tests**

```python
async def test_duplicate_verified_webhook_creates_one_receipt_and_one_completion(api_client, completed_job):
    first = await api_client.post(completed_job.webhook_url, content=completed_job.payload, headers=completed_job.headers)
    second = await api_client.post(completed_job.webhook_url, content=completed_job.payload, headers=completed_job.headers)
    assert first.json()["receipt_id"] == second.json()["receipt_id"]
    assert await completed_job.provider_completion_count() == 1

async def test_poll_after_webhook_keeps_the_same_terminal_provider_state(workflow):
    assert await workflow.webhook_then_poll() == "completed"
```

- [ ] **Step 2: Run webhook tests and confirm they fail**

Run: `pytest tests/integration/test_creative_webhook_ingress.py tests/integration/test_creative_control_api.py -q`

Expected: FAIL because no signed webhook route or canonical receipt exists.

- [ ] **Step 3: Implement verified, duplicate-safe receipt persistence**

Hash raw payload bytes before persistence; retain no secrets. Reject unknown providers, invalid signatures, malformed events, and provider/job mismatch. Insert-or-return the receipt, conditionally transition the provider job, emit audit/provenance/trace records, and let polling read the canonical state before requesting remote status.

- [ ] **Step 4: Verify**

Run: `pytest tests/integration/test_creative_webhook_ingress.py tests/integration/test_creative_provider_lifecycle.py tests/e2e/test_phase7_creative_recovery.py -q`

Expected: PASS. Commit: `feat: reconcile creative provider webhooks`.

### Task 7: Enforce registry-driven provider selection and bounded variants

**Files:**
- Modify: `src/salience/agents/creative.py`
- Modify: `src/salience/creative/capabilities.py`
- Modify: `src/salience/creative/repository.py`
- Modify: `src/salience/workflows/creative.py`
- Test: `tests/unit/test_creative_agents.py`
- Test: `tests/unit/test_creative_contracts.py`
- Test: `tests/e2e/test_phases_7_8_creative_loop.py`

**Interfaces:** `CreativeCapabilityRegistry.resolve(request, constraints)` returns the selected manifest and rejected-candidate reasons. `CreativeRepository.record_asset_variant` persists every produced variant and a selected/rejected reason.

- [ ] **Step 1: Write provider-selection red tests**

```python
def test_registry_selects_compatible_replacement_when_primary_is_disabled(registry, request):
    selection = registry.resolve(request, constraints=CreativeSelectionConstraints(max_variants=2))
    assert selection.selected.plugin_id == "replacement-fixture-creative"

def test_registry_rejects_an_explicit_unavailable_provider(registry, request):
    with pytest.raises(CapabilityNotSupported, match="explicit provider"):
        registry.resolve(request.model_copy(update={"provider_id": "disabled-provider"}))
```

- [ ] **Step 2: Run the registry/E2E suite and confirm it fails**

Run: `pytest tests/unit/test_creative_agents.py tests/unit/test_creative_contracts.py tests/e2e/test_phases_7_8_creative_loop.py -q`

Expected: FAIL because selection does not filter all declared constraints or persist variant decisions.

- [ ] **Step 3: Implement candidate filtering and variant persistence**

Pass Creative Director request and Production Agent bounded plan into registry resolution. Filter enabled/compatible manifests by capability, modality, controls, policy, explicit provider, formats, aspect ratio, duration, concurrency/rate state, budget, and variant quota. Submit each allowed variant with a stable derived request key, import/validate each result, and preserve non-selected variants with deterministic rejection reason.

- [ ] **Step 4: Verify**

Run: `pytest tests/unit/test_creative_agents.py tests/unit/test_creative_contracts.py tests/e2e/test_phases_7_8_creative_loop.py -q`

Expected: PASS for primary, replacement, unavailable, unsupported, explicit-provider, and quota cases. Commit: `feat: select creative providers by capability`.

### Task 8: Persist rights/provenance and enforce final-package policy

**Files:**
- Modify: `src/salience/creative/contracts.py`
- Modify: `src/salience/creative/governance.py`
- Modify: `src/salience/creative/repository.py`
- Modify: `src/salience/creative/service.py`
- Modify: `src/salience/workflows/creative.py`
- Test: `tests/integration/test_creative_rights_provenance.py`
- Test: `tests/evals/test_phase8_ready_package_eval.py`
- Test: `tests/e2e/test_phases_7_8_creative_loop.py`

**Interfaces:** `CreativeRightsContext` carries canonical IDs and scope facts. `CreativeRepository.record_asset_rights_and_provenance(...)` links rights and C2PA state to each asset. `CreativeService.finalize_ready_package` loads persisted evidence rather than trusting workflow literals.

- [ ] **Step 1: Write fail-closed red tests**

```python
@pytest.mark.parametrize("reason", ["missing_consent", "consent_revoked", "consent_expired", "territory_not_permitted", "voice_consent_missing"])
async def test_finalization_rejects_persisted_rights_blocker(service, production, reason):
    with pytest.raises(CreativeGovernanceDenied, match=reason):
        await service.finalize_ready_package(production.with_rights_blocker(reason))

async def test_required_c2pa_not_configured_blocks_finalization(service, production):
    with pytest.raises(CreativeGovernanceDenied, match="c2pa_required"):
        await service.finalize_ready_package(production.with_c2pa("not_configured", required=True))
```

- [ ] **Step 2: Run rights/provenance tests and confirm they fail**

Run: `pytest tests/integration/test_creative_rights_provenance.py tests/evals/test_phase8_ready_package_eval.py -q`

Expected: FAIL because the workflow does not persist or reload canonical rights/provenance links.

- [ ] **Step 3: Implement canonical links and required-policy evaluation**

Persist asset license, consent, likeness, voice, usage restriction, reference-asset lineage, provider/model, C2PA manifest reference, validation status, and signer metadata. Preserve `not_configured` exactly. Evaluate expiry/revocation/channel/territory/commercial permission from persisted rows at finalization. Require a real valid state only when the profile/policy marks C2PA required.

- [ ] **Step 4: Verify**

Run: `pytest tests/integration/test_creative_rights_provenance.py tests/evals/test_phase8_ready_package_eval.py tests/e2e/test_phases_7_8_creative_loop.py -q`

Expected: PASS. Commit: `feat: enforce creative rights provenance`.

### Task 9: Replace mutable decision upserts with immutable versions

**Files:**
- Modify: `src/salience/creative/repository.py`
- Modify: `src/salience/creative/service.py`
- Test: `tests/integration/test_distribution_package_repository.py`
- Test: `tests/integration/test_creative_release_gate_migration.py`

**Interfaces:** A changed title/thumbnail/localization/originality/disclosure/profile/approval selection returns a new distribution-package version and requires a new ready-package version; writers never issue a mutable upsert for approved lineage.

- [ ] **Step 1: Write direct and repository red tests**

```python
async def test_changed_title_after_ready_package_creates_new_distribution_and_ready_versions(repository, approved_package):
    replacement = await repository.create_distribution_revision(approved_package, selected_title="New title")
    assert replacement.distribution_package_id != approved_package.distribution_package_id
    assert replacement.version == approved_package.version + 1

def test_direct_update_of_ready_package_referenced_candidate_is_rejected(connection, approved_package):
    with pytest.raises(psycopg.errors.RaiseException):
        connection.execute("UPDATE title_thumbnail_candidates SET title = 'new' WHERE id = %s", (approved_package.title_candidate_id,))
```

- [ ] **Step 2: Run immutability tests and confirm they fail**

Run: `pytest tests/integration/test_distribution_package_repository.py tests/integration/test_creative_release_gate_migration.py -q`

Expected: FAIL because existing writers upsert mutable decision rows.

- [ ] **Step 3: Implement insert-only revisions**

Replace the `ON CONFLICT ... DO UPDATE` paths for governed decisions with exact-match read-or-insert behaviour before approval and explicit new package/version creation afterwards. Include final approval in the immutable referenced graph. Preserve original lineage and make finalization require the new approved version.

- [ ] **Step 4: Verify**

Run: `pytest tests/integration/test_distribution_package_repository.py tests/integration/test_creative_release_gate_migration.py tests/evals/test_phase8_ready_package_eval.py -q`

Expected: PASS. Commit: `fix: make approved distribution decisions immutable`.

### Task 10: Build the complete Gate-One end-to-end verifier and truthful documentation

**Files:**
- Modify: `scripts/verify-phases-7-8.sh`
- Modify: `tests/scripts/test_phase_7_8_documentation.sh`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/database.md`
- Modify: `docs/governance.md`
- Modify: `docs/recovery.md`
- Modify: `docs/workflows.md`
- Modify: `docs/verification.md`
- Modify: `docs/limitations.md`
- Modify: `docs/implementation-progress.md`
- Test: `tests/e2e/test_phase7_8_release_gate.py`

**Interfaces:** The verifier executes one fixture path covering durable reservation through immutable ready package and reports optional media/C2PA/live checks accurately.

- [ ] **Step 1: Write the comprehensive red E2E verifier**

```python
async def test_release_gate_survives_crash_and_converges_cost_webhook_rights_and_immutability(release_gate_scenario):
    result = await release_gate_scenario.run(crash_after="provider.accepted")
    assert result.provider_submit_count == 1
    assert result.reservation_count == result.actual_cost_entry_count == 1
    assert result.duplicate_webhook_receipt_count == 1
    assert result.ready_package_is_immutable is True
    assert result.reverse_lineage_complete is True
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest tests/e2e/test_phase7_8_release_gate.py -q`

Expected: FAIL until all five release blockers are implemented.

- [ ] **Step 3: Wire only verified checks into the project verifier**

Make `scripts/verify-phases-7-8.sh` run the release-gate E2E, migration, lifecycle, registry, rights, and immutability suites. It must retain `NOT RUN` for missing FFmpeg/C2PA/live configuration and must not label them `PASS`.

- [ ] **Step 4: Verify the Gate-One focused ladder**

Run: `bash tests/scripts/test_phase_7_8_documentation.sh && bash scripts/verify-phases-7-8.sh`

Expected: PASS for all required fixture checks with truthful optional status. Commit: `test: verify phase 7-8 release gate`.

### Task 11: Re-verify Phase 7–8 and complete independent review

**Files:**
- Modify: `docs/implementation-progress.md`
- Modify: `docs/verification.md`
- Create: `docs/superpowers/reviews/2026-09-13-phase-7-8-release-gate.md`

**Interfaces:** The review record names verifier evidence, checks all five original blockers, and reports `approved`, `blocked`, or `conditional` with concrete severity.

- [ ] **Step 1: Run the exact release verification ladder**

Run:

```bash
bash tests/scripts/test_phase_7_8_documentation.sh
bash scripts/verify-phases-7-8.sh
pytest tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q
python -m compileall -q src
git diff --check
node .agents/skills/archify/bin/archify.mjs validate architecture docs/salience-phase-1-8-final.architecture.json --quality showcase --repo-root . --json
```

Expected: every required command passes; optional FFmpeg/C2PA/live checks remain an accurate `NOT RUN` only when their prerequisites are absent.

- [ ] **Step 2: Run strongest Phase 1–6 regressions**

Run: `pytest tests/e2e/test_phase1_verifier.py tests/e2e/test_worker_restart.py tests/e2e/test_phases_1_4_stack.py tests/e2e/test_phase5_durable_research.py tests/e2e/test_phases_5_6_intelligence_loop.py -q`

Expected: PASS with no weakened or skipped required test.

- [ ] **Step 3: Perform an independent review against the spec**

Inspect migrations, raw SQL, fail-closed transitions, secrets, retries, tests, docs, and the full diff independently of the implementation sequence. Record the result and repair every critical/high finding before repeating Steps 1–2.

- [ ] **Step 4: Checkpoint the green barrier**

Update release status only after all evidence and review are green. Commit: `docs: certify phase 7-8 release gate`.

**Hard barrier:** Do not create a publisher migration, source module, API route, or test before this task is complete and its independent review is approved.

---

## Gate Two — Phase 9 Governed Publishing

### Task 12: Define publication contracts, credential leases, registry, and red tests

**Files:**
- Create: `src/salience/publication/__init__.py`
- Create: `src/salience/publication/contracts.py`
- Create: `src/salience/publication/credentials.py`
- Create: `src/salience/publication/registry.py`
- Test: `tests/unit/test_publication_contracts.py`
- Test: `tests/contracts/test_publisher_registry.py`

**Interfaces:** Defines immutable `PublisherAccount`, `PublisherConnection`, `PublisherCapabilityProfile`, `PublicationRequest`, `PublicationPlan`, `PublicationAttempt`, `RemotePublicationReceipt`, `Publication`, `CredentialLease`, and `PublisherAdapter` DTO/protocol contracts.

- [ ] **Step 1: Write red ownership and capability tests**

```python
def test_publication_request_requires_an_explicit_workspace_bound_account(ready_package):
    with pytest.raises(ValidationError, match="publisher_account_id"):
        PublicationRequest(ready_package_id=ready_package.id, publisher_account_id="", platform="youtube")

def test_registry_rejects_public_visibility_for_an_unaudited_profile(registry, request):
    with pytest.raises(PublisherCapabilityDenied, match="visibility"):
        registry.resolve(request.with_visibility("public"))

def test_credential_lease_is_redacted_and_cannot_be_serialized():
    assert "token" not in repr(CredentialLease("secret", expires_at)).casefold()
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_publication_contracts.py tests/contracts/test_publisher_registry.py -q`

Expected: FAIL because publication contracts and registry do not exist.

- [ ] **Step 3: Implement owned versioned contracts**

Require exact account and ready-package identities, explicit destination/locale/territory/visibility/schedule, capability-profile version, idempotency key, and approval reference. Credential records contain only a secret reference, required/granted scopes, expiry/refresh/revocation status, and no secret value. Registry filtering checks provider enablement, contract compatibility, platform/account type, audit state, scope, content type, visibility, disclosure support, quotas, and health.

- [ ] **Step 4: Verify and checkpoint**

Run: `pytest tests/unit/test_publication_contracts.py tests/contracts/test_publisher_registry.py -q && python -m compileall -q src && git diff --check`

Expected: PASS. Commit: `feat: define governed publication contracts`.

### Task 13: Add canonical publication migration and repository

**Files:**
- Create: `migrations/versions/0009_governed_publication.py`
- Create: `src/salience/publication/repository.py`
- Modify: `src/salience/db/models.py`
- Test: `tests/integration/test_publication_migrations.py`
- Test: `tests/integration/test_publication_repository.py`

**Interfaces:** Persists versioned accounts/connections/profiles/requests/plans/schedules/attempts/status events/webhook receipts/remote receipts/publications and their immutable links to `ReadyToPublishPackage@v1`, external effects, reservations, audit, provenance, and traces.

- [ ] **Step 1: Write migration red tests**

```python
async def test_ready_package_can_create_multiple_publication_requests_without_mutation(repository, ready_package, accounts):
    first = await repository.create_request(ready_package.id, accounts[0].id, "request-a")
    second = await repository.create_request(ready_package.id, accounts[1].id, "request-b")
    assert first.ready_package_id == second.ready_package_id == ready_package.id
    assert first.id != second.id

def test_remote_receipt_is_immutable(connection, receipt):
    with pytest.raises(psycopg.errors.RaiseException):
        connection.execute("UPDATE remote_publication_receipts SET remote_url = 'changed' WHERE id = %s", (receipt.id,))
```

- [ ] **Step 2: Run migration/repository tests and confirm failure**

Run: `pytest tests/integration/test_publication_migrations.py tests/integration/test_publication_repository.py -q`

Expected: FAIL because revision `0009` and publication repository are absent.

- [ ] **Step 3: Implement additive schema and idempotent writes**

Create immutable/versioned tables with workspace and tenant hooks. Add unique request/effect/attempt/receipt keys, safe webhook payload hashes, status history, account scope, profile facts/source timestamps, publish schedule mapping, remote identifiers, metadata hash, disclosure projection, cost links, and reverse lineage. Add triggers that reject changes to immutable request version, remote receipt, and publication receipt references.

- [ ] **Step 4: Verify**

Run: `alembic upgrade head && pytest tests/integration/test_publication_migrations.py tests/integration/test_publication_repository.py -q`

Expected: PASS. Commit: `feat: persist governed publications`.

### Task 14: Implement publisher policy, private delivery, fixture adapter, and red lifecycle tests

**Files:**
- Create: `src/salience/publication/governance.py`
- Create: `src/salience/publication/delivery.py`
- Create: `src/salience/publication/providers.py`
- Test: `tests/unit/test_publication_governance.py`
- Test: `tests/unit/test_publication_delivery.py`
- Test: `tests/integration/test_fixture_publisher.py`

**Interfaces:** `PublicationAuthorizer.reauthorize`, `PublicationDelivery.create`, `FixturePublisherAdapter`, and `PublisherAdapter` methods for preflight/prepare/create/resume/submit/reconcile/status/cancel/webhook/capability refresh.

- [ ] **Step 1: Write red policy and fixture tests**

```python
async def test_preflight_denies_revoked_connection_before_fixture_submission(authorizer, request):
    assert (await authorizer.reauthorize(request.with_connection_status("revoked"))).allowed is False

async def test_fixture_crash_after_acceptance_reconciles_one_remote_post(provider, request):
    accepted = await provider.submit(request)
    assert (await provider.reconcile(request.idempotency_key)).remote_id == accepted.remote_id
    assert provider.submit_count == 1

def test_delivery_url_is_short_lived_and_asset_scoped(delivery):
    receipt = delivery.create(asset_id="asset-1", expires_in=timedelta(minutes=5))
    assert receipt.asset_id == "asset-1"
    assert receipt.expires_at > now_utc()
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_publication_governance.py tests/unit/test_publication_delivery.py tests/integration/test_fixture_publisher.py -q`

Expected: FAIL because publishing policy, delivery, and fixture adapter are absent.

- [ ] **Step 3: Implement deterministic external-effect safety**

Reauthorize exact ready package/account/profile/destination/locale/territory/policy/rights/disclosure/approval/budget/rate quota immediately before an effect. Reuse canonical cost repository. The fixture emits accepted, processing, published, failed, cancelled, quota, timeout, duplicate-webhook, and ambiguous results. Delivery receipts carry a private asset reference and expiry, never storage credentials.

- [ ] **Step 4: Verify**

Run: `pytest tests/unit/test_publication_governance.py tests/unit/test_publication_delivery.py tests/integration/test_fixture_publisher.py -q`

Expected: PASS. Commit: `feat: add governed fixture publisher`.

### Task 15: Build Temporal publication workflow, scheduling, receipts, and recovery verifier

**Files:**
- Create: `src/salience/workflows/publication.py`
- Modify: `src/salience/workflows/worker.py`
- Modify: `src/salience/workflows/persistence.py`
- Test: `tests/e2e/test_phase9_publication_recovery.py`
- Test: `tests/e2e/test_phase9_governed_publishing.py`

**Interfaces:** `PublicationWorkflowRequest`, `PublicationActivities`, `GovernedPublicationWorkflow`, and `PublicationScheduleService` use canonical publication plans and existing Temporal scheduling.

- [ ] **Step 1: Write red worker-restart and race tests**

```python
async def test_publication_restart_after_remote_acceptance_does_not_duplicate_post(scenario):
    result = await scenario.run(crash_after="publication.accepted")
    assert result.fixture_submit_count == 1
    assert result.publication_state == "published"
    assert result.remote_receipt_count == 1

async def test_poll_and_duplicate_webhook_converge_to_one_receipt(scenario):
    result = await scenario.webhook_then_poll_then_duplicate_webhook()
    assert result.status_events == ["accepted", "processing", "published"]
    assert result.webhook_receipt_count == 1
```

- [ ] **Step 2: Run E2E tests and confirm failure**

Run: `pytest tests/e2e/test_phase9_publication_recovery.py tests/e2e/test_phase9_governed_publishing.py -q`

Expected: FAIL because no publication workflow exists.

- [ ] **Step 3: Implement checkpointed publication activities**

Create/checkpoint request, preflight, cost reserve, plan, delivery, submit-or-reconcile, await, receipt, settlement, and complete/cancel/dead-letter stages. Schedule immutable request/plan references, preserve scheduler restart safety, and create a new plan for changed scheduling input. Persist conditional state transitions before remote calls; ambiguous outcomes become `ambiguous_requires_reconciliation`, never a blind repeat.

- [ ] **Step 4: Verify**

Run: `pytest tests/e2e/test_phase9_publication_recovery.py tests/e2e/test_phase9_governed_publishing.py tests/e2e/test_phase7_creative_recovery.py -q`

Expected: PASS. Commit: `feat: add durable governed publication workflow`.

### Task 16: Expose publication control API, CLI, SDK, and signed webhook ingress

**Files:**
- Create: `src/salience/api/routes/publication.py`
- Modify: `src/salience/api/app.py`
- Modify: `src/salience/api/dependencies.py`
- Modify: `src/salience/api/schemas.py`
- Modify: `src/salience/cli.py`
- Modify: `src/salience/sdk/client.py`
- Test: `tests/integration/test_publication_control_api.py`
- Test: `tests/unit/test_cli.py`
- Test: `tests/unit/test_sdk.py`

**Interfaces:** Scoped request/plan/schedule/inspect/cancel endpoints and commands; `POST /v1/publishers/{provider_id}/webhooks` verifies and deduplicates a signed receipt. There is no API for raw credentials or package mutation.

- [ ] **Step 1: Write public-surface red tests**

```python
async def test_publication_start_requires_ready_package_and_explicit_account(api_client):
    response = await api_client.post("/v1/publications/requests", json={"ready_package_id": "ready-1"})
    assert response.status_code == 422

async def test_publisher_webhook_duplicate_returns_same_receipt(api_client, signed_webhook):
    first = await api_client.post(signed_webhook.url, content=signed_webhook.body, headers=signed_webhook.headers)
    second = await api_client.post(signed_webhook.url, content=signed_webhook.body, headers=signed_webhook.headers)
    assert first.json()["receipt_id"] == second.json()["receipt_id"]
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/integration/test_publication_control_api.py tests/unit/test_cli.py tests/unit/test_sdk.py -q`

Expected: FAIL because publication routes and clients are absent.

- [ ] **Step 3: Implement HTTP-only operator surfaces**

Require `control:write` for request/schedule/cancel and `control:read` for inspection. Return canonical IDs, state, trace, safe reason, and receipt IDs only. Validate workspace/account/ready-package binding. CLI and SDK must call the same routes; neither connects to PostgreSQL. Reject any payload carrying token/secret fields.

- [ ] **Step 4: Verify**

Run: `pytest tests/integration/test_publication_control_api.py tests/unit/test_cli.py tests/unit/test_sdk.py -q`

Expected: PASS. Commit: `feat: expose governed publication control surface`.

### Task 17: Add opt-in YouTube adapter, documentation, architecture visual, and release verification

**Files:**
- Create: `src/salience/publication/youtube.py`
- Create: `tests/integration/test_youtube_publisher_contract.py`
- Create: `tests/live/test_youtube_publisher_smoke.py`
- Create: `scripts/verify-phase-9.sh`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/api.md`
- Modify: `docs/database.md`
- Modify: `docs/dependencies.md`
- Modify: `docs/deployment.md`
- Modify: `docs/governance.md`
- Modify: `docs/recovery.md`
- Modify: `docs/workflows.md`
- Modify: `docs/verification.md`
- Modify: `docs/limitations.md`
- Modify: `docs/phase-9-handoff.md`
- Modify: `docs/implementation-progress.md`
- Create: `docs/adr/0006-governed-publishing.md`
- Create: `docs/salience-phase-1-9.architecture.json`
- Create: `docs/salience-phase-1-9.architecture.html`

**Interfaces:** `YouTubePublisherAdapter` uses injected `CredentialLease`, private-only capability profile, resumable session identity, and owned DTOs. The live smoke test is opt-in and private/test-only.

- [ ] **Step 1: Write adapter and live-status red tests**

```python
async def test_youtube_adapter_starts_resumable_session_without_persisting_bearer_token(adapter, request):
    attempt = await adapter.prepare_upload(request)
    assert attempt.upload_session_id
    assert "Bearer" not in attempt.model_dump_json()

def test_live_youtube_smoke_reports_not_run_without_explicit_configuration(capsys):
    assert run_live_smoke() == "NOT RUN: YOUTUBE_PUBLISHER_CONNECTION_REF is not configured"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/integration/test_youtube_publisher_contract.py tests/live/test_youtube_publisher_smoke.py -q`

Expected: FAIL because the adapter and status reporting are absent.

- [ ] **Step 3: Implement the disabled-by-default official adapter**

Use `POST /upload/youtube/v3/videos?uploadType=resumable` only with an injected valid lease, explicit connection/profile, private visibility allowance, and adapter configuration. Persist only a safe resumable session identifier and remote response IDs. Map HTTP failures to typed provider errors and reconcile session/video IDs before retry. The live test uses private/test visibility only, never runs in normal CI, and treats missing credentials/configuration as `NOT RUN`.

- [ ] **Step 4: Run Phase 9 verification and independent review**

Run:

```bash
bash scripts/verify-phase-9.sh
pytest tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q
python -m compileall -q src
git diff --check
node .agents/skills/archify/bin/archify.mjs validate architecture docs/salience-phase-1-9.architecture.json --quality showcase --repo-root . --json
pytest -m live tests/live/test_youtube_publisher_smoke.py -q
```

Expected: required fixture checks pass; the live smoke prints only `PASS`, `FAIL`, or `NOT RUN: <reason>`; no public post is attempted without explicit operator configuration. Perform a fresh independent review, repair all critical/high findings, re-run the same command block, update all truthfulness docs and visual evidence, then commit: `docs: complete governed publishing phase`.

## Plan Self-Review

- **Spec coverage:** Tasks 1–11 cover each mandatory Phase 7–8 blocker, exact re-verification, and independent review before the hard Phase-9 barrier. Tasks 12–17 cover account/credential separation, capabilities, adapter contract, delivery, policy, cost, effects, schedules, webhooks, immutable receipt, fixture recovery, YouTube boundary, API/CLI/SDK, docs, visual, and review.
- **Placeholder scan:** Every task identifies paths, interfaces, red assertions, a failure command, implementation details, a passing command, and a checkpoint commit.
- **Type consistency:** Creative lifecycle contracts feed the cost, repository, provider, workflow, and webhook tasks. Publication contracts feed migration, registry, adapter, workflow, and public API tasks. Both domains consume the same canonical effect/cost/audit/provenance/trace boundaries.

## Execution Handoff

Execute Gate One inline, task by task, with a commit and progress checkpoint after each green verifier. Create an isolated worktree before changing implementation files. Do not begin Gate Two unless Task 11 is green and independently approved.
