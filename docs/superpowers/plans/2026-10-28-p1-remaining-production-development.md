# P1 Remaining Production Development Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the remaining effects-disabled P1 production-development contracts and a local RG0/RG1 evidence package checker while leaving production unavailable and all external acceptance held.

**Architecture:** Extend the authenticated publication-worker launcher, the current `ProductionEffectDispatch`, and the existing schedule/case state machines. Add production-only recovery and schedule-binding records after the actual 0046 head, then use injected provider-neutral adapters and deterministic fakes to prove their contracts. The final checker validates exact-candidate evidence; it cannot contact targets or change gate state.

**Tech Stack:** Python 3.13, Pydantic, Psycopg, PostgreSQL, Alembic, Temporal SDK, pytest, canonical JSON/SHA-256.

**Specs:** [Production routing and worker fencing](../specs/2026-10-13-p1-production-routing-and-worker-fencing-design.md); [live schedule fencing](../specs/2026-10-13-p1-live-schedule-fencing-design.md); [production recovery and alerts](../specs/2026-10-13-p1-production-recovery-and-alerts-design.md); [external RG0/RG1 acceptance](../specs/2026-10-13-rg0-rg1-external-acceptance-design.md); [Slice 2 policy and revocation](../specs/2026-10-13-p1-production-policy-and-revocation-design.md).

## Global Constraints

- Production effects remain disabled and fixture publication budget remains zero.
- No live endpoint, production database, credential, provider account, Temporal schedule, alert destination, or external route service is used by this plan's development tests.
- RG0 and full RG1 remain `HELD`; P2 remains `BLOCKED`; `main` remains unmerged.
- The preserved source baseline is branch `arch` at `0763702bbedda7ffc99c3b526a5d018340e63aae`; the actual migration head is `0046_production_policy_approval_id_immutability`.
- `0043_complete_publication_recovery_liabilities` remains the historical fixture floor. Add migrations 0047–0051 in one lineage from 0046; never rewrite, remove, or reinterpret Slice 2 history.
- Keep fixture-only guards in `worker.py`, `TemporalFixtureLegacyScheduleControl`, fixture schedule/source tables, fixture notification/timer paths, and the existing fixture hold verifier.
- `route_id` is the stable UUID already required by `RouteBinding` and migration 0044. Compute a separate canonical `route_tuple_sha256`; include it and `route_id` in the authenticated route manifest. Never change historical route IDs or derive a UUID from a digest.
- Use the existing launcher, lock, compatibility gate, `ProductionEffectDispatch`, `CycleScheduleCutover`, typed recovery cases, immutable fixture holds, and P1 envelope. Do not create a generic worker, a second artifact registry, a new policy engine, or a second case state machine.
- Platform-specific route lease, Temporal, provider-read, and alert implementations are injected later. This development adds contracts and deterministic fakes only. Missing or unavailable injected adapters fail closed.
- No provider write is permitted. Remote-read fakes may return only deterministic synthetic results. A claimed or uncertain prior operation remains a liability.
- No migration backfills or adopts fixture or unmarked historical rows as production, creates enrollment provenance from caller JSON/session settings/timestamps, or infers an owner, route, target, deadline, provider result, credential, or no-effect proof.
- Local test, CI, formal-approval, external-runtime, and historical-candidate evidence remain distinct. Every implementation evidence record binds the exact source commit, tree, artifacts, route manifest, and migration head that produced it.
- Each task is an independent review stop: after its focused suite passes and commit is made, obtain a fresh review of that task's diff and evidence before starting the next task. Any fix is a task-local follow-up commit.
- The checker emits `PASS`, `FAIL`, `NOT RUN`, `HELD`, or aggregate `NO_GO` only. It cannot enable production, release a gate, authorize P2, or merge to `main`.

## Review Focus

- Missing, duplicate, malformed, or mismatched route manifest fields must deny before Temporal connection or worker registration. Pin with Task 1 `test_route_manifest_rejects_incomplete_duplicate_or_mismatched_entries` and Task 5 `test_route_preflight_denies_before_temporal_connect`.
- Stale owner epochs, transfer races, or lease-service outage must stop polling and deny each later action while preserving a previously consumed claim. Pin with Task 4 `test_fake_route_authority_linearizes_transfer_and_one_use_decisions` and Task 5 `test_route_renewal_loss_drains_before_expiry_and_preserves_identity`.
- A not-found or partial provider response without a documented authoritative visibility window must remain unknown and cannot trigger retry. Pin with Task 7 `test_remote_read_not_found_without_authoritative_window_stays_held`.
- Missing, stale, replayed, or cross-operation no-effect proof must not advance a permit generation; a valid command preserves the original operation, fingerprint, and provider idempotency key. Pin with Task 8 `test_no_effect_generation_advance_requires_one_typed_same_operation_command`.
- Incomplete mixed-source rollback inventory, an unsupported snapshot, or an unavailable readback must block prior-code execution. Pin with Task 9 `test_rollback_v2_requires_complete_two_reason_source_inventory_before_exec`.

---

## Baseline File Map

The plan reuses the current authenticated start path in `scripts/launch-v4-publication-worker.py`, `deployment/v4/publication-worker-launch-lock.json`, `src/salience/cycles/publication_recovery_compatibility.py`, and `src/salience/workflows/worker.py`. `src/salience/cycles/production_dispatch.py` already defines `RouteBinding`, `RouteFenceAuthority`, and `ProductionEffectDispatch`; extend those contracts in place and preserve current Slice 2 behavior.

The schedule state machine stays in `src/salience/cycles/schedule_cutover.py`; `src/salience/cycles/temporal_schedule_control.py` remains fixture-only. New production schedule controls live in `src/salience/cycles/production_schedule_control.py`. New production recovery work/read/alert behavior lives in `src/salience/cycles/production_recovery.py`; the existing `src/salience/cycles/publication_recovery.py` fixture verifier remains unchanged while a separately named v2 verifier is added.

Add five migrations as one additive lineage from the actual head 0046: `migrations/versions/0047_p1_production_recovery_work_events.py`, `0048_p1_production_schedule_bindings.py`, `0049_p1_production_recovery_decision_evidence.py`, `0050_p1_production_dispatch_reissue.py`, and `0051_p1_recovery_rollback_v2.py`. Task 7 owns 0049 because the 0047 recovery-event guard rejects the separate workspace/goal stop and typed deadline-command evidence required by its events; 0049 extends that guard without changing 0047 history. Task 8 then owns 0050, and Task 9 owns 0051 and pins the final launcher v2 candidate to that exact head. Add the local package contract in `src/salience/acceptance/p1_external_evidence.py`, its CLI in `scripts/check-p1-external-acceptance.py`, and its JSON Schema in `docs/v4/p1-rg0-rg1-external-acceptance.schema.json`. Focused tests belong in `tests/unit/test_production_routing.py`, `tests/integration/test_v4_migrations.py`, `tests/integration/test_p1_production_routing.py`, `tests/integration/test_p1_production_schedule_fencing.py`, `tests/integration/test_p1_production_recovery.py`, `tests/integration/test_p1_production_policy_revocation.py`, and `tests/unit/test_p1_external_acceptance.py`.

## Tasks

### Task 1: Canonical Route Manifest and Tuple Evidence

**Files:**

- Create: `src/salience/cycles/production_routing.py`
- Modify: `src/salience/cycles/production_dispatch.py:RouteBinding`, `RouteFenceRequest`, `_route_request`, `_permit_snapshot`, `_request_from_snapshot`
- Create: `tests/unit/test_production_routing.py`
- Modify: route fixture builder in `tests/integration/test_p1_production_policy_revocation.py`

**Interfaces:**

- Produce `RouteTupleV1(deployment_id: UUID, environment_id: UUID, temporal_endpoint_id: str, namespace: str, task_queue: str, worker_kind: str, workflow_types: tuple[str, ...], activity_types: tuple[str, ...], producer_bindings: tuple[Mapping[str, str], ...], compatibility_mode: str)`.
- Produce `canonical_route_tuple_bytes(value: RouteTupleV1) -> bytes`, using sorted JSON keys, compact separators, UTF-8, and exactly one final newline.
- Produce `route_tuple_sha256(value: RouteTupleV1) -> str`.
- Canonicalize semantic sets before serialization: workflow/activity type names are sorted unique arrays; each producer-binding mapping has sorted keys; producer-binding entries are sorted by their canonical JSON bytes. Reject duplicate entries rather than relying on caller order.
- Produce `ProductionRouteManifestV1(route_id: UUID, route_tuple: RouteTupleV1, route_tuple_sha256: str, slots: tuple[UUID, ...], artifact_sha256: str, image_sha256: str, artifact_lock_sha256: str)`, `canonical_route_manifest_bytes(value: ProductionRouteManifestV1) -> bytes`, and `route_manifest_sha256(value: ProductionRouteManifestV1) -> str`. Canonicalize slots by sorted unique UUID string; exclude the manifest digest itself from the bytes being hashed.
- Produce `parse_route_manifest(payload: Mapping[str, object], *, expected_lock_sha256: str) -> ProductionRouteManifestV1` and `parse_route_bundle(payload: Mapping[str, object], *, expected_lock_sha256: str) -> tuple[ProductionRouteManifestV1, ...]`. The bundle parser rejects empty/duplicate route IDs; the manifest parser rejects duplicate slots and malformed/unknown fields.
- Extend `RouteBinding` with `route_tuple_sha256: str | None = None` so historical v1 snapshots remain readable; require a lowercase SHA-256 for every new routed permit. `route_id` remains a required UUID and is not derived from the tuple digest. The authenticated manifest digest binds the UUID and tuple hash together.
- Retain route-fence request identity as `(deployment_id, environment_id, route_id, slot_id, owner_id, fencing_epoch, route_manifest_sha256, artifact_lock_sha256, operation_id, permit_id, action_kind)`; the tuple hash is authenticated by `route_manifest_sha256` and is also recorded in the permit snapshot.

- [ ] **Step 1: Write failing tests** `test_route_id_is_stable_uuid_and_tuple_digest_is_canonical`, `test_route_manifest_digest_authenticates_route_uuid_and_tuple_hash`, `test_route_manifest_rejects_incomplete_duplicate_or_mismatched_entries`, and `test_v1_permit_snapshot_remains_readable_without_tuple_hash`. Assert canonical sets and mapping keys hash identically regardless of input order; `route_id` parses as UUID and remains an independent field; changed queue/producer/workflow binding changes the tuple digest; bad lock digest, missing slot, duplicate route ID/slot/type/binding, unknown field, malformed digest, and caller-selected queue reject; legacy v1 snapshots still parse but cannot be used to issue new production permits.
- [ ] **Step 2: Run the tests** with `.venv/bin/python -m pytest -p no:cacheprovider tests/unit/test_production_routing.py -q`; expected: import/API failures.
- [ ] **Step 3: Implement** canonical route tuple/bundle/manifest serialization and parsing in `production_routing.py`; extend `RouteBinding` and permit snapshots without changing any old route UUID or 0044–0046 record.
- [ ] **Step 4: Re-run the focused tests**; expected: all route canonicalization and rejection cases pass, and current Slice 2 unit tests still construct the updated `RouteBinding`.
- [ ] **Step 5: Commit** with `feat: define authenticated production route manifest`.

### Task 2: Add Migration 0047 for Production Recovery Work and Events

**Files:**

- Create: `migrations/versions/0047_p1_production_recovery_work_events.py`
- Modify: `tests/integration/test_v4_migrations.py`
- Preserve unchanged: `migrations/versions/0044_production_policy_revocation.py`, `0045_production_policy_approval_guard_repair.py`, and `0046_production_policy_approval_id_immutability.py`

**Interfaces:**

- Set `revision = "0047_p1_production_recovery_work_events"`, `down_revision = "0046_production_policy_approval_id_immutability"`.
- Create `v4_production_intent_enrollments`, the sole typed immutable production-origin authorization record, keyed uniquely by `(intent_id, eligibility_revision)`. Bind intent and eligibility revision; workspace, goal and goal revision; source owner; subject and subject revision; canonical nonempty sorted unique grant IDs; policy version and document hash; workspace and goal stop revisions; artifact SHA-256; route UUID, slot UUID, route owner UUID, fencing epoch, route-manifest digest, external artifact-lock digest, and exactly one route-fence decision ID. Enforce same-scope foreign keys, positive revisions/epoch, valid hashes, uniqueness, and append-only immutability. Revoke general insert/update/delete; the Task 6 production schedule/admission writer alone may enroll after live-current policy, stop, and route-fence checks. Do not backfill or infer origin from shared Slice 2 rows, caller JSON, a session setting, or timestamps.
- Create `v4_production_recovery_work` with `work_id`, `source_discriminant`, `source_id`, `workspace_id`, `goal_id`, `intent_id`, nullable `cycle_id`/`context_id`/`operation_id`, source/case/eligibility revisions, `owner_id`, `target_alias`, nullable remote account/resource identity, typed reason/state, request/source fingerprints, snapshot version and full snapshot JSON/hash, deadline and recipient-profile revisions, and immutable creation time. Every work item must join to the exact production enrollment. Source IDs are `case_id` for `cycle_case`, `intent_id` for `pre_admission_intent_case`, and `operation_id` for a self-contained `production_publication_hold` whose stable hold identity is `work_id`. Do not reference or adopt the fixture hold row. Enforce the three source variants; `pre_admission_intent_case` has no cycle, context, operation, or provider target. `production_publication_hold` is itself immutable at state `held`.
- Type the source reason/state pairs exactly: `cycle_case` copies one allowlisted `failure_class` (`policy_denial`, `technical_failure`, `quality_failure`, `unknown_effect`, `admission_review`, `deadline`, `manual_review`) and one nonterminal case state (`retry_due`, `reconciling`, `rework_due`, `awaiting_review`, `suspended`); `production_publication_hold` uses reason `expired_permit_receipt` or `published_during_cancel` and state `held`; `pre_admission_intent_case` uses reason `admission_deferred` or `admission_review_required` mapped to the exact `v4_admissions.disposition`, and state `unadmitted`. Preserve the original case state machine.
- A production hold uses snapshot version `salience.p1-production-publication-liability.v1` and exactly these 22 canonical keys: `production_intent_enrollment`, `goal`, `intent`, `cycle`, `context`, `job`, `production_current_dispatch`, `production_permits`, `production_permit_claims`, `publication_request`, `publication_plan`, `publication_attempt`, `external_effect`, `budget_reservation`, `output`, `remote_publication_receipts`, `publication_status_events`, `cycle_events`, `cycle_outbox`, `job_checkpoints`, `audit_events`, `provenance_records`. The historical fixture-only `salience.publication-liability.v1` remains unchanged and is not a production snapshot. Reject extra, missing, or incomplete keys. Cases and pre-admission intents use database-derived source fingerprints and NULL liability snapshots.
- Create append-only `v4_production_recovery_events` with `event_id`, `work_id`, unique `event_key`, typed `event_kind`, UTC timestamp, actor/route/slot/epoch, policy-decision reference, bounded payload JSON, and payload SHA-256. Include the typed `same_operation_no_effect_reissue` event kind; Task 7 owns its writer, and Task 8 owns its one-use permit consumption.
- Add foreign keys and guards to preserve enrollment/source/workspace/operation identity, reject unmarked or fixture-to-production adoption, unsafe payload keys, malformed hashes, updates/deletes, and duplicate idempotency keys. Do not backfill any source.

- [ ] **Step 1: Write failing migration and source-schema tests** in `tests/integration/test_v4_migrations.py`: `test_0047_upgrade_preserves_0046_authority_history`, `test_0047_production_intent_enrollment_requires_complete_current_authority`, `test_0047_production_intent_enrollment_is_immutable_and_has_no_backfill`, `test_0047_recovery_source_discriminants_and_snapshots_are_guarded`, `test_0047_recovery_events_are_append_only_and_scoped`, `test_0047_downgrade_refuses_populated_recovery_history`, and `test_0047_empty_downgrade_restores_0046_head`. Include SQL-level cases for exact source IDs/reasons/states, the exact 22-key production snapshot, rejection of missing enrollment, and unchanged historical unmarked rows. Seed fixture holds/cases/notifications and Slice 2 authority rows at 0046; assert bytes and identities remain unchanged.
- [ ] **Step 2: Run the focused tests** against a disposable 0046 database. RED must be explicit: Alembic cannot resolve 0047 and the enrollment relation is absent; tests fail rather than skip. Verify each enrollment, work, and snapshot contract is actually exercised.
- [ ] **Step 3: Implement** only the additive enrollment, recovery-work, and recovery-event relations and guards. The restricted Task 6 writer is the only enrollment insertion path. Downgrade may drop new relations only after a locked proof that all three relations and referenced production work are empty; populated or uncertain history raises before DDL or `alembic_version` changes.
- [ ] **Step 4: Re-run the named tests**; expected: historical rows are unchanged, incomplete/stale/direct enrollment attempts deny, work rows without the exact enrollment deny, source IDs/reasons/states and all 22 snapshot keys are exact, populated downgrade changes nothing, and empty downgrade returns exactly to 0046.
- [ ] **Step 5: Commit** with `feat: add production recovery work and event schema`.

### Task 3: Add Migration 0048 for Production Schedule Inventory and Bindings

**Files:**

- Create: `migrations/versions/0048_p1_production_schedule_bindings.py`
- Modify: `tests/integration/test_v4_migrations.py`
- Preserve unchanged: fixture schedule tables, constraints, triggers, and `TemporalFixtureLegacyScheduleControl`

**Interfaces:**

- Set `revision = "0048_p1_production_schedule_bindings"`, `down_revision = "0047_p1_production_recovery_work_events"`.
- Create exactly four additive relations: `v4_production_schedule_inventories`, `v4_production_schedule_inventory_pages`, `v4_production_schedule_inventory_members`, and `v4_production_schedule_source_bindings`. Do not create schedules, import fixture snapshots, or backfill remote identities.
- `v4_production_schedule_inventories` columns are `inventory_id`, `target_alias`, `namespace`, `captured_at`, `source_tool`, `source_tool_version`, `candidate_sha256`, `configuration_sha256`, `page_count`, `remote_member_count`, `bound_member_count`, `out_of_scope_member_count`, `inventory_page_chain_sha256`, `member_set_sha256`, `inventory_sha256`, and nullable `finalized_at`. Successful finalization, not a writable completeness boolean, represents a complete inventory.
- `v4_production_schedule_inventory_pages` columns are `inventory_id`, nullable `remote_schedule_id` (NULL means top-level listing page; a value means history page), `page_index`, `request_cursor_sha256`, sanitized `page_items` JSONB, `response_sha256`, `item_count`, `next_cursor_sha256`, `is_terminal`, `item_set_sha256`, `last_item_sha256`, and derived `page_receipt_sha256`. A unique expression key on `(inventory_id, COALESCE(remote_schedule_id, ''), page_index)` prevents duplicate indexes. Listing `page_items` are sorted `{remote_schedule_id, member_sha256}` rows matching members assigned to the page. History `page_items` contain exact sanitized action keys `action_id_sha256`, `scheduled_slot`, `started_at`, `completed_at`, `outcome_class`, and `workflow_id_sha256`. Histories sort by `(scheduled_slot, action_id_sha256)`; the member action-set root hashes all action identity digests in that order, and its watermark/identity digest equal the final action. SQL checks response hash, array count, sorted item-set root, page receipt hash, and listing-page/member equality. Persist no raw cursor, credential, or secret. The first listing cursor digest is `SHA256("salience.p1-production-schedule-inventory.start.v1")`; the first history cursor digest is `SHA256("salience.p1-production-schedule-history.start.v1:" || remote_schedule_id)`.
- `v4_production_schedule_inventory_members` columns are `inventory_id`, `remote_schedule_id`, listing `page_index`, `classification` (`bound|out_of_scope` only), canonical `member_snapshot` JSONB, `member_sha256`, `history_page_count`, `history_action_count`, `history_page_chain_sha256`, `history_action_set_sha256`, `history_watermark`, and `history_watermark_sha256`. The versioned `salience.p1-production-schedule-inventory-member.v1` snapshot has exactly these keys: `schema_version`, `target_alias`, `namespace`, `remote_schedule_id`, `classification`, `source_kind`, `schedule_id`, `publication_schedule_id`, `workspace_id`, `goal_id`, `goal_revision`, `source_actor_id`, `source_owner_subject_id`, `schedule_operator_subject_id`, `owner_ref`, `exclusion_reason`, `workflow_type`, `action_id`, `argument_sha256`, `task_queue`, `route_id`, `route_tuple_sha256`, `slot_id`, `owner_id`, `fencing_epoch`, `route_manifest_sha256`, `artifact_lock_sha256`, `cadence_policy_revision`, `interval_seconds`, `first_v4_slot`, `observed_last_slot`, `remote_state`, `next_action`, `running_action_count`, `history_page_count`, `history_action_count`, `history_page_chain_sha256`, `history_action_set_sha256`, `history_watermark`, and `history_watermark_sha256`. SQL enforces this exact key set and source-ID null matrix. Out-of-scope rows have NULL source IDs and non-empty `owner_ref` plus `exclusion_reason`; no `unknown` classification can finalize.
- `v4_production_schedule_source_bindings` stores exact member identity and facts, inventory ID/digest, source approval ID, source actor, source-owner and operator subject IDs, workspace/goal/revision, route UUID and tuple digest, route/lock digests, cadence/first slot, and immutable binding fingerprint. Exact source matrix: `source_kind` is only `intelligence_research` or `governed_publication`; `schedule_id` is always non-null and matches `v4_native_schedule_sources.schedule_id` or `v4_native_publication_schedule_sources.schedule_id` plus canonical `job_schedules.id`; `publication_schedule_id` is NULL for intelligence and required for governed publication, matching the native row and `publication_schedules.id` whose `job_schedule_id=schedule_id`. `source_actor_id` is distinct from named source-owner identity.
- A SQL `v4_finalize_production_schedule_inventory_v1(inventory_id)` runs under lock after pages, members, bindings, and typed approvals are present. It verifies listing and per-schedule history indexes are exactly `0..page_count-1`, exact first-page sentinels, cursor-hash links, exactly one terminal final page, `item_count=jsonb_array_length(page_items)`, recomputed response/item-set/page-receipt hashes, listing page items matching member rows, and page counts summing to persisted member/action counts. It validates the bound/out-of-scope partition, unique remote IDs, and sorted page/member roots. Each digest is lowercase hex SHA-256 over UTF-8 canonical JSONB text using `encode(public.digest(convert_to(payload::text, 'UTF8'), 'sha256'), 'hex')`. The page receipt is the canonical JSONB object with exactly `inventory_id`, `remote_schedule_id`, `page_index`, `request_cursor_sha256`, `response_sha256`, `item_count`, `next_cursor_sha256`, `is_terminal`, `item_set_sha256`, and `last_item_sha256`; listing root hashes ordered page-receipt digests, each history root hashes that member's ordered page receipts, and member root hashes ordered `{remote_schedule_id, member_sha256}` objects. The final inventory root hashes exactly `schema_version`, `inventory_id`, `target_alias`, `namespace`, `captured_at`, `source_tool`, `source_tool_version`, `candidate_sha256`, `configuration_sha256`, `page_count`, `remote_member_count`, `bound_member_count`, `out_of_scope_member_count`, `inventory_page_chain_sha256`, and `member_set_sha256`. Approval IDs are excluded to avoid a circular digest; each binding approval binds the computed inventory ID/digest. For every bound member, SQL recomputes the complete sorted action-identity root and the terminal history page's `last_item_sha256` equals its `history_watermark_sha256`; the finalizer enumerates current active native intelligence/publication sources and proves both directions between eligible source rows and exactly one bound member. Out-of-scope rows require owner and reason and have no binding. A deferred constraint trigger at commit rechecks finalized inventories. Exactly one matching binding is required per bound member and none per out-of-scope member; source-kind IDs, all member facts, and typed approval context must match. Finalized rows are immutable.
- Add exact typed approval schema `salience.p1-production-schedule-binding-approval.v1` in `approval_requests`, with `effect_type='production_schedule_binding'`. Its `request_context` has exactly these keys and no others: `schema_version`, `target_alias`, `namespace`, `inventory_id`, `inventory_sha256`, `source_kind`, `schedule_id`, `publication_schedule_id`, `workspace_id`, `goal_id`, `goal_revision`, `source_actor_id`, `source_owner_subject_id`, `schedule_operator_subject_id`, `remote_schedule_id`, `task_queue`, `workflow_type`, `action_id`, `argument_sha256`, `route_id`, `route_tuple_sha256`, `slot_id`, `owner_id`, `fencing_epoch`, `route_manifest_sha256`, `artifact_lock_sha256`, `cadence_policy_revision`, `first_v4_slot`, `history_watermark_sha256`, `policy_version_id`, `policy_document_hash`, and `expires_at`. The row's workspace/policy IDs must equal the payload values. The 0048 guard checks approved status, non-null `decided_by` and `decided_at`, exact row ID/workspace/effect/policy/expiry, and prohibits adding the tag to an existing generic row or changing/deleting decided identity. Task 6 rechecks current operator grants and policy/stop/route facts. This new exact-shape/identity guard does not edit migrations 0044–0046.
- Enforce one production binding per goal and one per remote identity `(target_alias, namespace, remote_schedule_id)`. Task 3 owns all four schemas, guard/finalizer functions, immutable approval identity, and SQL completeness proof; Task 6 is the only application writer. Remote authenticity and named-owner qualification remain external `NOT RUN`/`HELD`.

- [ ] **Step 1: Write failing migration tests** in `tests/integration/test_v4_migrations.py`: `test_0048_upgrade_preserves_fixture_schedule_contracts`, `test_0048_production_schedule_binding_uniqueness_and_immutability`, `test_0048_finalized_inventory_page_member_binding_immutability`, `test_0048_incomplete_or_fixture_derived_binding_is_rejected`, `test_0048_upgrade_does_not_backfill_from_fixture_sources`, `test_0048_inventory_finalize_rejects_partial_or_truncated_pages`, `test_0048_inventory_finalize_rejects_cursor_chain_or_terminal_mismatch`, `test_0048_inventory_finalize_rejects_count_or_sorted_root_mismatch`, `test_0048_inventory_rejects_duplicate_or_unknown_members`, `test_0048_inventory_finalize_requires_complete_bound_member_history`, `test_0048_source_kind_schedule_id_publication_schedule_id_matrix`, `test_0048_binding_requires_exact_member_and_schedule_approval`, `test_0048_deferred_finalization_requires_one_binding_per_bound_member`, `test_0048_out_of_scope_member_requires_owner_and_reason`, `test_0048_schedule_approval_shape_and_decided_identity_are_immutable`, `test_0048_downgrade_refuses_adopted_schedule_evidence`, and `test_0048_empty_downgrade_restores_0047_head`. These prove fixture schema/guards are unchanged and no 0048 row is backfilled from fixture records.
- [ ] **Step 2: Run the focused migration tests** against a disposable database; expected: revision 0048 is unresolved and every 0048 case fails rather than skips.
- [ ] **Step 3: Implement** the four additive relations, typed approval guard, SQL digest/cursor/count finalizer, and deferred completeness validator. No fixture DDL changes or backfill. Populated or uncertain downgrade refuses before DDL or `alembic_version` mutation.
- [ ] **Step 4: Re-run all named migration tests**; expected: incomplete pages/history, chain/count/root mismatch, invalid source matrix, approval mismatch, unknown/duplicate members, and incomplete binding sets deny; exact finalized inventories pass; binding and finalized receipt rows remain immutable; populated downgrade changes nothing; empty downgrade returns exactly to 0047; every fixture contract remains unchanged.
- [ ] **Step 5: Commit** with `feat: add production temporal schedule bindings`.

### Task 4: Define Route Lease Lifecycle and Deterministic Authority Fake

**Files:**

- Modify: `src/salience/cycles/production_routing.py`
- Create test-only fake: `tests/support/production_route_authority.py`
- Create: `tests/integration/test_p1_production_routing.py`

**Interfaces:**

- Produce `RouteLeaseClaim(route_id: UUID, route_manifest_sha256: str, artifact_lock_sha256: str, slot_id: UUID, owner_id: UUID, build_id: str)` and `RouteLease(route_id: UUID, slot_id: UUID, owner_id: UUID, fencing_epoch: int, expires_at: datetime, route_manifest_sha256: str, artifact_lock_sha256: str)`.
- Produce `RouteLeaseAuthority` protocol with `claim_slot(request: RouteLeaseClaim) -> RouteLease`, `renew_slot(lease: RouteLease) -> RouteLease`, `authorize_action(request: RouteFenceRequest) -> RouteFenceDecision`, `release_slot(lease: RouteLease) -> RouteSlotReadback`, `terminate_slot(lease: RouteLease) -> RouteSlotReadback`, and `read_slot(route_id: UUID, slot_id: UUID) -> RouteSlotReadback`.
- `RouteLeaseAuthority` structurally satisfies the existing `RouteFenceAuthority.authorize_action` contract; define it without a runtime import cycle and do not create a second action-decision contract.
- Every method is versioned as `salience.p1-route-fence.v1`, uses the authority's clock and one serialization order, and denies missing/unavailable/stale state. The application receives this protocol by dependency injection; do not add a production adapter or credentials.
- The test fake uses an explicit lock, monotonic epochs, unique one-use decision UUIDs, short decision expiry, and exact tuple matching. It lives under `tests/` and is not an application default.

- [ ] **Step 1: Write failing tests** `test_route_claim_is_compare_and_swap_for_each_slot`, `test_fake_route_authority_linearizes_transfer_and_one_use_decisions`, `test_route_decision_rejects_stale_owner_and_artifact_drift`, and `test_route_service_outage_denies_without_cached_epoch`. Assert concurrent claims produce one owner; prior decisions cannot authorize a different action/operation/epoch; transfer before action denies; consumed decisions remain one unchanged in-flight liability.
- [ ] **Step 2: Run the focused tests**; expected: missing protocol/fake failures.
- [ ] **Step 3: Implement** the protocol and test fake. Do not add an application lease table or a live service client.
- [ ] **Step 4: Re-run the tests**; expected: linearized fake behavior passes and no live adapter is imported.
- [ ] **Step 5: Commit** with `feat: define injectable route lease authority`.

### Task 5: Bind Authenticated Worker Startup, Registration, Drain, and Restart

**Files:**

- Modify: `scripts/launch-v4-publication-worker.py:_parser`, `main`, `_environment_for_child`, and preflight payload
- Modify: `scripts/build_publication_worker_launch_lock.py`
- Modify: `src/salience/cycles/publication_recovery_compatibility.py:_validate_lock_and_pin`, `_validate_migration_inventory`, `_validate_migration_graph`
- Modify: `src/salience/workflows/publication.py:PublicationWorkflowState` only as needed to inject the routed dispatcher and exact permit-request factory
- Modify: `src/salience/workflows/worker.py:build_deployable_worker`, `build_routed_worker`, and `run_worker`
- Modify: `tests/integration/test_v4_publication_recovery_floor.py` and `tests/integration/test_p1_production_routing.py`

**Interfaces:**

- Preserve existing launch-lock v1, pin fields, external launcher path, and 0043 fixture verification. Add a versioned v2 lock payload that authenticates a `ProductionRouteManifestV1`, exact production migration head, and closed workflow/activity allowlists with the same independent external SHA-256 trust digest.
- Produce `build_routed_worker(client, *, route: ProductionRouteManifestV1, lease: RouteLease, route_authority: RouteLeaseAuthority, database_url: str, publication_state: PublicationWorkflowState, permit_request_factory: Callable[[ProductionApprovalBinding, PublicationEffectBinding], ProductionPermitRequest]) -> Worker`. It injects `ProductionEffectDispatch(database_url, route_authority)` and the exact route-bound permit factory into `PublicationWorkflowState`, registers exactly the route's allowlisted types on its exact namespace/task queue, and validates route/lease/build/artifact bindings before registration.
- Keep `run_worker()` fixture-only. It continues to reject `SALIENCE_DEPLOYMENT_MODE != "fixture"`; routed worker assembly is internal and dependency-injected for local acceptance. No environment flag, default endpoint, real credential, or unqualified adapter enables it.
- Startup validates source/import/artifact/migration pins before importing `salience.workflows.worker`; then resolves one manifest entry, claims a slot, passes the route tuple and one-use lease identity to the child, revalidates before Temporal connection, renews at no more than one third of TTL, stops polling before TTL on renewal loss, drains active work without deleting identity, and requires exact readback before transfer.
- Produce `TemporalWorkerReadback.read_worker(route_id: UUID, slot_id: UUID) -> WorkerReadback`, an injected, target/namespace-bound complete registration/drain readback. `WorkerReadback` includes owner/epoch, route and artifact digests, namespace/queue, registered type sets, poller count, active-task count, observation time, and completeness. It has no mutation method.

- [ ] **Step 1: Write failing tests** `test_route_preflight_denies_before_temporal_connect`, `test_worker_registers_exact_route_allowlists_and_namespace`, `test_route_renewal_loss_drains_before_expiry_and_preserves_identity`, `test_duplicate_slot_owner_never_registers`, `test_routed_publication_uses_production_effect_dispatch_with_exact_route_binding`, `test_restart_preserves_workflow_cycle_and_operation_identity`, and `test_0043_fixture_startup_guard_remains_unchanged`. Use fake launcher files, fake Temporal connector, and Task 4 authority only.
- [ ] **Step 2: Run the focused launcher/worker tests**; expected: v2 route keys and `build_routed_worker` are absent.
- [ ] **Step 3: Implement** v2 parsing on the existing authenticated launcher and an injected routed-worker assembly path. Keep fixture startup and the historical local lock as v1. Verify that exact production migration head is authenticated; allow 0043 only for the existing fixture compatibility check.
- [ ] **Step 4: Re-run the focused tests**; expected: malformed/unknown route, missing authority, stale lease, renewal loss, mismatched target, and wrong migration head deny before connect/registration; the fixture startup suite is unchanged.
- [ ] **Step 5: Commit** with `feat: add authenticated route-bound worker startup`.

### Task 6: Add Production Schedule Adapter and Binding Controller

**Files:**

- Create: `src/salience/cycles/production_schedule_control.py`
- Modify: `src/salience/cycles/schedule_cutover.py:CycleScheduleCutover` only to accept the shared schedule-control protocol
- Modify: `tests/integration/test_p1_production_schedule_fencing.py`
- Modify: `tests/integration/test_p1_production_recovery.py`
- Preserve: `src/salience/cycles/temporal_schedule_control.py:TemporalFixtureLegacyScheduleControl`

**Interfaces:**

- Produce `TemporalScheduleTarget` protocol with target/namespace-bound `list_page(page_token)`, `history_page(remote_schedule_id, page_token)`, `describe(remote_schedule_id)`, `pause(remote_schedule_id, command_key)`, and `resume_after(remote_schedule_id, command_key, start_at)` methods. Listing is a scoped inventory query; history reads and pause/resume are limited to one exact remote schedule. History returns sanitized action facts and cursor receipts only. No create/update/cadence-selection or provider-effect method is exposed.
- Produce `ScheduleAlertSink.emit_hold(workspace_id: UUID, source_id: UUID, reason_code: str, evidence_sha256: str) -> None` in this module. Task 7's `ProductionRecoveryAlerts` implements it; Task 6 tests inject a deterministic sink fake.
- Produce `ProductionTemporalScheduleControl(database_url: str, *, workspace_id: UUID, target_alias: str, namespace: str, route: ProductionRouteManifestV1, route_lease: RouteLease, route_authority: RouteLeaseAuthority, target: TemporalScheduleTarget, alerts: ScheduleAlertSink)` implementing paginated inventory/history capture, exact source binding, `describe`, `pause`, and `resume_after` against all four 0048 relations. Its restricted writer persists page/member receipts, validates the exact typed approval, inserts one binding per bound member, and calls the SQL finalizer only after current source, `cycles:schedule` grant, policy, stop, and route checks pass. Each remote mutation requires the exact finalized member/binding identity and a fresh route/policy/stop decision.
- Produce a restricted `ProductionIntentEnrollmentWriter` in this module as part of the production schedule/admission path. Its typed operation receives the canonical intent key and current route-authority handle, not a production boolean or caller JSON. It reloads intent/goal/workspace/goal revision, source owner, subject/revision/grants, policy/version/hash, both stop revisions, artifact, route/slot/owner/epoch and lock/manifest digests from authoritative records; obtains one fresh route-fence decision for the exact tuple; rechecks policy, stops, and route ownership before commit; and atomically writes the unique 0047 enrollment with the authorized schedule/admission source transition. No stale, absent, or ambiguous fact may be defaulted. Use deterministic fake policy/stop/route authorities; no live target is configured.
- The enrollment writer is the only production-origin creator. Task 7 is downstream: it may read and consume an exact matching enrollment but cannot create, adopt, or backfill one. Task 6 tests must cover atomic commit/replay and denial on stale policy, either stop revision, changed route/slot/owner/epoch/digest, missing facts, and direct/general insert attempts.
- Produce `ProductionScheduleCutover` that calls existing `CycleScheduleCutover.prepare`, `activate`, `poll`, and `rollback` with the production control protocol and immutable source binding. Do not fork its cursor, idempotency, pending/active/rollback-pending/rolled-back state machine.
- Before each action, validate target/namespace/remote ID, full inventory digest, source/owner/revision/action/route binding, current stop and policy, and current route fence. On timeout, reconcile only the same remote ID and command key; unknown state remains fenced and emits an alert event.

- [ ] **Step 1: Write failing tests** in `tests/integration/test_p1_production_schedule_fencing.py` for every schedule acceptance item: `test_live_inventory_requires_complete_target_scoped_two_sided_binding`, `test_production_binding_does_not_relax_fixture_target_guards`, `test_pending_requires_remote_pause_readback_and_drained_actions`, `test_lost_pause_ack_or_source_readback_reconciles_one_exact_schedule`, `test_manual_event_and_scheduled_same_slot_coalesce_after_restart`, `test_stop_or_revocation_racing_activation_and_poll_fails_closed`, `test_out_of_band_unpause_or_action_route_drift_holds_and_alerts`, `test_crash_at_each_cutover_boundary_recovers_from_durable_state`, `test_unresolved_v4_work_or_remote_liability_blocks_rollback`, `test_rollback_requires_later_slot_readback_before_prior_route_restart`, `test_candidate_and_environment_mismatch_never_passes_live_acceptance`, and `test_schedule_controller_has_no_effect_or_schedule_creation_authority`. Add `test_production_inventory_writer_persists_listing_and_history_receipts`, `test_production_inventory_writer_rejects_incomplete_or_mismatched_roots_before_finalize`, `test_production_schedule_binding_rechecks_current_cycles_schedule_policy_stops_and_route`, `test_production_schedule_admission_enrolls_intent_atomically_with_current_authority`, `test_intent_enrollment_rejects_stale_policy_stop_or_route`, and `test_intent_enrollment_replay_is_exact_and_conflicting_revisions_deny`; exercise enrollment/recovery handoff from `tests/integration/test_p1_production_recovery.py`. Use deterministic fakes with paginated listing/history, lost acknowledgments, route/stop drift, and no effect method. Task 3 migration-shape tests remain in `tests/integration/test_v4_migrations.py`.
- [ ] **Step 2: Run the focused schedule tests**; expected: no production adapter/binding implementation exists.
- [ ] **Step 3: Implement** the injected production control and wrapper over the current cutover/cursor. Keep fixture target and queue checks in force.
- [ ] **Step 4: Re-run the focused tests** plus `tests/integration/test_v4_schedule_cutover.py`, `test_v4_native_schedules.py`, and `test_v4_native_publication_schedules.py`; expected: all production fake scenarios pass and fixture behavior remains unchanged. The named-environment run remains external `NOT RUN`.
- [ ] **Step 5: Commit** with `feat: add injected production schedule control`.

### Task 7: Add Production Recovery Reads, Typed Commands, Deadlines, and Alerts

**Files:**

- Create: `src/salience/cycles/production_recovery.py`
- Create: `migrations/versions/0049_p1_production_recovery_decision_evidence.py`
- Modify: `src/salience/cycles/production_dispatch.py` only for a typed recovery-command reference consumed in Task 8
- Modify: `tests/integration/test_p1_production_recovery.py`
- Modify: `tests/integration/test_v4_migrations.py` for the additive 0049 recovery-event evidence guard and safe downgrade
- Preserve: fixture `publication_recovery.py`, `RuntimeWaits`, `FixtureWaitDriver`, `FixtureNotificationSink`, and current `v4_recovery_cases` transitions

**Interfaces:**

- Produce `ProductionRemoteReadTarget.read_operation(request: ProductionRemoteReadRequest) -> ProductionRemoteObservation`; request contains only original operation UUID, original provider idempotency key, account/target/resource IDs, request fingerprint, and current scoped read grant. The adapter has no write, cancel, broad list, or cross-account method.
- Produce `ProductionRecoveryAlerts.send(alert: ProductionAlertRequest) -> ProductionAlertReceipt`, `.lookup(idempotency_key: str) -> ProductionAlertReceipt | None`, `.acknowledge(command: ProductionAlertAcknowledgment) -> ProductionAlertEvent`, and `.emit_hold(workspace_id: UUID, source_id: UUID, reason_code: str, evidence_sha256: str) -> None` implementing Task 6's `ScheduleAlertSink`. Acknowledgment requires exact alert/work binding. The injected fake is deterministic; recipient and retry policy must be explicit versioned inputs with no defaults.
- Produce `ProductionRecoveryWorkRepository`, `ProductionRecoveryCoordinator.reconcile(work_id: UUID)`, and `ProductionRecoveryCaseController` using `v4_production_recovery_work`, `v4_production_recovery_events`, and existing canonical case/intent/command records.
- Add migration `0049_p1_production_recovery_decision_evidence`, revision `0049_p1_production_recovery_decision_evidence`, down revision `0048_p1_production_schedule_bindings`. The verified 0047 validator rejects the split workspace/goal stop evidence and typed deadline-command receipt required by Task 7. Extend the event validation additively: require separate positive workspace and goal stop revisions plus stage/policy/route decision IDs for no-effect events, and require policy/route decision evidence plus the exact event key and typed command ID for deadline-suspension events. Do not rewrite 0047 or any 0044–0048 history.
- Every recovery source must join its exact immutable `v4_production_intent_enrollments` row: case via its intent and eligibility revision; hold via its production intent/current dispatch generation; and pre-admission work via its intent and eligibility revision. Exact `source_id`, reason, state, and production snapshot rules are fixed by the recovery spec. Missing or historical unmarked sources remain inventory gaps; this task cannot infer production origin or create enrollments.
- Produce `ProductionRecoveryCaseController.authorize_no_effect_generation_reissue(command: NoEffectGenerationReissueCommand) -> ProductionNoEffectReissueReceipt`. It accepts only an exact provider-specific authoritative absence observation after its documented visibility window, matching current case revision, original operation/request fingerprint, original idempotency key, and current stage/policy/stop/route decisions. It appends one typed `same_operation_no_effect_reissue` event; it does not retry, change case state, create an operation, or call a provider write.
- Deadline wakes are deduplicated by `(source_discriminant, source_id, source_revision, due_at, action_kind)` in the event ledger and may call only the current typed case/intent command paths. Alert acknowledgment remains receipt only.

- [ ] **Step 1: Write failing tests** for recovery acceptance items 1–9 and 11–12 in `tests/integration/test_p1_production_recovery.py`: `test_exact_source_and_stable_work_identity`, `test_recovery_source_requires_matching_production_enrollment`, `test_source_ids_reasons_states_and_production_hold_snapshot_are_exact`, `test_unmarked_historical_rows_are_never_adopted`, `test_original_operation_read_scope`, `test_authority_and_stop_races`, `test_result_classification`, `test_crash_and_exact_replay`, `test_production_case_command_and_deadline_controller`, `test_alert_delivery_and_acknowledgment`, `test_owner_and_outage_escalation`, `test_two_reason_concurrent_hold_insert_and_crash`, `test_operationless_intent_restart_and_concurrency`, and `test_schema_and_migration_safety`. Include `test_remote_read_not_found_without_authoritative_window_stays_held`, `test_pre_admission_intent_never_constructs_remote_client`, and `test_no_effect_command_binds_same_operation_key_and_revision`. In `tests/integration/test_v4_migrations.py`, add red-first 0049 cases named `test_0049_requires_split_stop_and_decision_evidence_for_no_effect_events`, `test_0049_requires_typed_command_receipt_for_deadline_suspension`, `test_0049_preserves_0047_event_history_and_fixture_contracts`, `test_0049_downgrade_refuses_when_decision_evidence_exists`, and `test_0049_empty_downgrade_restores_0047_event_validator`.
- [ ] **Step 2: Run the focused recovery and 0049 migration tests**; expected on the pre-Task-7 base: production work repository/controllers/adapters and revision 0049 are absent, and the unchanged 0047 event guard rejects the exact split-stop and typed deadline-command evidence cases.
- [ ] **Step 3: Implement** the additive 0049 validator extension, injected read/alert protocols, exact enrollment-bound source consumption, append-only event writes, deadline controller, and typed no-effect command writer. Reject unsupported/unknown provider responses and any unmarked historical source. Use one-use event identity; no application adapter defaults to a live provider. A populated downgrade refuses before changing the validator whenever new decision/command evidence exists; an empty downgrade restores the exact 0047 event validator. Never modify migration 0047.
- [ ] **Step 4: Re-run the focused recovery tests and the named 0049 cases in `tests/integration/test_v4_migrations.py`**; expected: every read and alert remains scoped, replay-safe, bounded, and evidence-only; unsupported or unknown results stay held; split-stop/deadline evidence is accepted only with the exact decision and typed-command receipts; downgrade refuses before guard replacement when new evidence exists; fixture timer/notification and hold tests remain unchanged. Named target reads, delivery, and acknowledgment remain external `NOT RUN`.
- [ ] **Step 5: Commit** with `feat: add production recovery and alert contracts`.

### Task 8: Extend Slice 2 Dispatch for Typed Same-Operation Reissue

**Files:**

- Create: `migrations/versions/0050_p1_production_dispatch_reissue.py`
- Modify: `src/salience/cycles/production_dispatch.py:ProductionPermitRequest`, `ProductionEffectDispatch.issue_permit`, `_issue_permit`, `_permit_snapshot`, `_request_from_snapshot`, `_validate_permit_row`
- Modify: `tests/integration/test_v4_migrations.py`
- Modify: `tests/integration/test_p1_production_policy_revocation.py`

**Interfaces:**

- Set `revision = "0050_p1_production_dispatch_reissue"`, `down_revision = "0049_p1_production_recovery_decision_evidence"`.
- Keep permit snapshot v1 readable without rewriting existing rows. Emit `salience.p1-production-effect-permit.v2` for new permits with `route_tuple_sha256`, `generation`, and nullable `recovery_command_id`; SQL insertion validation accepts historical v1 only as stored history and requires v2 for new rows after 0050.
- Add nullable `recovery_command_id` FK from `v4_production_effect_permits` to `v4_production_recovery_events(event_id)` and a unique partial index so one no-effect command can authorize at most one next-generation permit.
- Replace `v4_advance_production_current_dispatch_v1` only through this additive migration. It permits `generation = 1` with no command as before; generation `n+1` requires expected current generation `n`, the exact one-use typed event from Task 7, matching operation/request fingerprint/idempotency key/prior generation, current claimed liability, and a fresh current policy/stop/route check. The pointer advances atomically; all prior permits/claims remain append-only and queryable.
- Extend `ProductionPermitRequest` with `generation: int = 1` and `recovery_command_id: UUID | None = None`. `ProductionEffectDispatch` still reloads all authority on the primary, obtains a fresh route decision, and refuses unknown/accepted/unresolved prior generations. It never changes operation ID or provider idempotency key.
- Add `ProductionRecoveryCaseController.issue_no_effect_generation_permit(command, *, request: ProductionPermitRequest, dispatch: ProductionEffectDispatch) -> ProductionPermitResult`. It obtains/replays the exact typed command event, copies the request with `generation = current_generation + 1` and that event ID, and calls `ProductionEffectDispatch.issue_permit`; dispatch revalidates the current database facts and consumes the event once. A crash between event append and permit insertion replays the same command ID and cannot mint a second permit.

- [ ] **Step 1: Write failing tests** `test_0050_preserves_all_predecessor_history`, `test_0050_new_permit_snapshot_v2_binds_route_tuple_hash`, `test_no_effect_generation_advance_requires_one_typed_same_operation_command`, `test_no_effect_command_cannot_be_replayed_or_cross_operation`, `test_unknown_or_accepted_generation_cannot_be_reissued`, `test_generation_advance_preserves_original_operation_fingerprint_and_key`, `test_0050_populated_downgrade_refuses_without_mutation`, and `test_0050_empty_downgrade_restores_0049_contract`.
- [ ] **Step 2: Run the focused tests**; expected: 0050 is unresolved and current dispatch rejects generation advancement.
- [ ] **Step 3: Implement** the additive column/index/function changes and v2 parser/writer. Do not edit migrations 0044–0049 or rewrite historical v1 snapshots. A populated downgrade raises before any mutation; empty downgrade may restore the 0049 contract.
- [ ] **Step 4: Re-run the tests** plus the existing `tests/integration/test_p1_production_policy_revocation.py` suite; expected: policy/approval/stop/claim invariants remain green, one correctly typed same-operation no-effect reissue succeeds, all invalid proofs deny, and fixture mode separation stays intact.
- [ ] **Step 5: Commit** with `feat: gate production permit reissue on typed no-effect proof`.

### Task 9: Add Complete Rollback Verifier v2 and Launcher ABI v2

**Files:**

- Create: `migrations/versions/0051_p1_recovery_rollback_v2.py`
- Create: `src/salience/cycles/p1_recovery_rollback.py`
- Create: `scripts/verify-p1-recovery-rollback-v2.py`
- Modify: `scripts/launch-v4-publication-worker.py:main`, rollback parser, authenticated lock v2 preflight
- Modify: `scripts/build_publication_worker_launch_lock.py:build_publication_worker_launch_lock` to finalize the v2 candidate payload at the last planned migration head
- Modify: `src/salience/cycles/publication_recovery_compatibility.py` to accept only the exact v2 verifier ABI for production prior-code restart
- Modify: `tests/integration/test_v4_publication_recovery_floor.py` for v2 launcher lock construction and preflight
- Modify: `tests/integration/test_p1_production_routing.py` for route-bound launcher and worker readback integration
- Modify: `tests/integration/test_p1_production_recovery.py` for complete inventory and recovery cases
- Modify: `tests/integration/test_v4_migrations.py` for migration 0051 and downgrade safety

**Interfaces:**

- Set migration `revision = "0051_p1_recovery_rollback_v2"`, `down_revision = "0050_p1_production_dispatch_reissue"`.
- Add `public.v4_p1_recovery_rollback_inventory_v2(workspace_id uuid) RETURNS jsonb`, which discovers all in-scope records itself and returns exact migration head, sorted source identities, per-source/per-reason counts including explicit zero counts, snapshot versions/hashes, full liability fingerprints, production permit/claim/current-pointer references, all four production schedule inventory/page/member/binding relations and tagged schedule approvals, recovery work/events, and unresolved/missing-link blockers. Both reasons `expired_permit_receipt` and `published_during_cancel` are queried on every call.
- The 0051 inventory validates schedule inventory roots/counts/finalization, complete sorted listing and history receipt roots, exact source-kind/ID pairs, typed approval IDs/fingerprints, and blockers for incomplete inventories, missing/duplicate bindings, out-of-scope/binding mismatch, unbound members, approval mismatch, or missing history pages. It discovers these records from SQL scope, never caller-supplied IDs.
- Include every `v4_production_intent_enrollments` row in the complete inventory and validate its exact source joins, immutable route/policy/stop binding, and enrollment key wherever recovery work or a production hold refers to it. Include Task 7's 0049 split workspace/goal stop revisions, stage/policy/route decision IDs, typed deadline command receipts, and event payload digests in the complete event inventory. Production hold snapshots use the exact 22-key contract from Task 2; fixture v1 snapshots remain a separate supported historical source.
- Produce `verify_p1_recovery_rollback_v2(database_url: str, *, workspace_id: UUID, route_authority: RouteLeaseAuthority, temporal_readback: TemporalWorkerReadback) -> RecoveryRollbackInventoryV2`. It validates the complete database inventory, current route lease/owner readback through Task 4, and complete Temporal registration/drain readback through Task 5; unknown route action, unavailable authority, incomplete source query/readback, unsupported snapshot, changed liability, or nonmatching pre/post digest denies.
- Launch-lock v2 `rollback-restart` accepts only the exact workspace scope; it rejects caller-supplied hold/operation ID lists. Before importing prior application code, it runs the verifier; after process exit/restart, it reads the complete inventory again and requires an unchanged digest. Keep fixture lock v1 and `verify-v4-publication-recovery-rollback.py` historical fixture behavior unchanged.
- Update and regenerate the authenticated v2 launcher candidate lock/payload after migration 0051 is defined. Its canonical payload must require the exact final migration head `0051_p1_recovery_rollback_v2` and the exact rollback-verifier-v2 ABI; pin this with `test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi` in `tests/integration/test_v4_publication_recovery_floor.py`. Regenerate the deterministic development candidate payload and SHA-256 with the existing builder, while keeping the external production trust digest unprovisioned and the payload explicitly development-only until external qualification. Preserve the tracked v1 fixture lock unchanged.
- Migration 0051 downgrade always raises before any schema mutation or `alembic_version` change. Pin this with `test_0051_downgrade_raises_without_schema_or_alembic_version_change` in `tests/integration/test_v4_migrations.py`: snapshot the complete schema inventory/fingerprint and version row at 0051, attempt downgrade, assert it raises, and assert both snapshots remain exactly unchanged with head still `0051_p1_recovery_rollback_v2`. Operational rollback disables new production permits, keeps schema/history and route stopped, and never uses a 0043-only or 0046-only artifact as production-compatible.

- [ ] **Step 1: Write failing tests** in `tests/integration/test_p1_production_recovery.py`: `test_rollback_v2_requires_complete_two_reason_source_inventory_before_exec`, `test_rollback_v2_checks_zero_one_and_many_rows_for_each_reason`, `test_rollback_v2_rejects_partial_inventory_and_omitted_rows`, `test_rollback_v2_rejects_snapshot_or_liability_change`, and `test_rollback_v2_rejects_unknown_route_claim_and_unavailable_readback`; in `tests/integration/test_p1_production_routing.py`: `test_launcher_v2_rejects_caller_supplied_hold_lists` and `test_launcher_v2_runs_pre_and_post_readback_before_prior_code`; in `tests/integration/test_v4_publication_recovery_floor.py`: `test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi`; and in `tests/integration/test_v4_migrations.py`: `test_0051_downgrade_raises_without_schema_or_alembic_version_change`.
- [ ] **Step 2: Run those focused verifier, launcher, and migration tests**; expected: v2 verifier and exact-0051 final-head ABI are absent, and 0051 cannot yet be resolved.
- [ ] **Step 3: Implement** the additive complete inventory and v2 read-only verifier, then connect it to the existing authenticated launcher v2 path. The verifier itself never changes state or resolves liabilities.
- [ ] **Step 4: Re-run all four named test files**; expected: every invalid/missing/unknown shape blocks before process execution, both reason predicates always run, valid zero/one/many inventories produce deterministic equal pre/post digests, the authenticated v2 candidate payload requires exactly migration head 0051 and verifier ABI v2, attempted downgrade raises with schema fingerprint and `alembic_version` unchanged at 0051, and fixture v1 remains unchanged.
- [ ] **Step 5: Commit** with `feat: add complete recovery rollback verifier v2`.

### Task 10: Implement the Local External Acceptance Package Checker

**Files:**

- Create: `src/salience/acceptance/p1_external_evidence.py`
- Create: `scripts/check-p1-external-acceptance.py`
- Create: `docs/v4/p1-rg0-rg1-external-acceptance.schema.json`
- Create: `tests/unit/test_p1_external_acceptance.py`

**Interfaces:**

- Produce `validate_acceptance_package(payload: Mapping[str, object], *, now: datetime) -> AcceptanceDisposition` and CLI `scripts/check-p1-external-acceptance.py --package <path> --schema <path>`.
- Validate one immutable manifest plus exactly 22 row IDs (five RG0 and 17 P1/RG1), exact full commit/tree/artifact/deployment/environment/migration identities, owner/operator/reviewer authorization, evidence URI/digests, executable pinned procedure, raw-result/readback references, freshness policy, planned transition or `NO_MUTATION`, and dependency-matched status.
- Reconcile row IDs/gates and current hold status read-only against `docs/v4/p0-release.json` and `docs/v4/p1-qualification-matrix.json`; never update those ledgers or infer a PASS from child evidence.
- Keep `route_id` a UUID and require `route_tuple_sha256` plus authenticated route-manifest digest where route evidence applies. Preserve historical fixture/CI/formal-approval scopes as non-external.
- A missing target, owner, credential identity, runbook, evidence digest, readback, or freshness limit returns `NOT RUN`/`HELD` and aggregate `NO_GO`. The CLI does not contact external systems or modify release/matrix files.

- [ ] **Step 1: Write failing tests** for all 20 package/checker acceptance cases named in the external-acceptance spec: `test_package_requires_exact_candidate_tree_artifacts_deployment_and_environment`, `test_package_requires_exact_row_set_once`, `test_each_row_requires_named_authorized_owner_operator_and_independent_reviewer`, `test_procedure_is_pinned_and_executable`, `test_raw_results_and_rollback_are_hashed_and_referenced`, `test_scope_transfer_across_fixture_ci_commit_environment_account_is_rejected`, `test_ci_formal_approval_and_external_acceptance_remain_distinct`, `test_fail_not_run_held_stale_or_scope_mismatch_blocks_package`, `test_missing_or_unapproved_freshness_limit_holds_mutable_evidence`, `test_planned_revocation_with_verified_final_denial_passes`, `test_unplanned_or_subsequent_revision_change_fails`, `test_no_mutation_requires_equal_start_and_end_state`, `test_revoked_credentials_are_never_restored`, `test_run_invalidation_is_append_only_and_transitive`, `test_negative_controls_pass_only_on_proven_denial_and_zero_effect`, `test_package_cannot_enable_production_or_release_a_gate`, `test_final_audit_is_separate_and_rechecks_every_row`, `test_rollback_requires_pre_state_action_and_exact_post_state_readback`, `test_observability_artifacts_are_correlated_and_secret_free`, and `test_default_package_adds_no_application_migration_and_excludes_later_phases`.
- [ ] **Step 2: Run the focused checker tests**; expected: acceptance module/schema/CLI are absent.
- [ ] **Step 3: Implement** schema validation, immutable digest checks, transitive invalidation, scope/status validation, and CLI output. No external client or gate mutation method is included.
- [ ] **Step 4: Re-run the tests**; expected: valid synthetic packages pass only their local structural checks; every missing, stale, mismatched, unsafe, or external-unavailable condition remains `NO_GO`; 22 rows are required once each.
- [ ] **Step 5: Commit** with `feat: add p1 external acceptance package checker`.

### Task 11: Run Exact-Candidate Development Acceptance and Record Evidence

**Files:**

- Create: `docs/v4/p1-remaining-production-development-evidence.json`
- Create/update: `.superpowers/sdd/2026-10-28-p1-remaining-production-development/progress.md`
- Create: `.superpowers/sdd/2026-10-28-p1-remaining-production-development/final-evidence-report.md`

**Interfaces:**

- Evidence records full source commit/tree, candidate artifact and lock digests, route-manifest digest, migration head, exact local command/exit/result, fixture/fake scope, and reviewer disposition.
- `candidate_source_commit` is the implementation commit whose tests and artifacts were measured; `evidence_commit` is recorded separately after adding this report so the evidence does not attempt to hash its own commit.
- Record each of the five RG0 external checks and all 17 RG1 rows as `NOT RUN` or `HELD` until exact target, owners, credentials, and independent evidence exist. Never promote a fake, fixture, CI, or historical observation to external `PASS`.
- Update only the new planning/execution ledger and evidence file; do not rewrite the frozen initial gap audit, Slice 2 evidence, historical 0043 evidence, P0/P1 gate state, or main-branch records.

- [ ] **Step 1: Run focused acceptance suites** from Tasks 1–10 against disposable services: route manifest/launcher, migration 0047–0051, schedule fake, recovery fake, policy/revocation, rollback v2, and checker tests. Record exact commands, exits, counts, and source tree. The exact local candidate uses head `0051_p1_recovery_rollback_v2`; this development pin does not establish external qualification.
- [ ] **Step 2: Run the required non-live project verification** for the final exact candidate and confirm production mode remains disabled and fixture budget zero. Do not run any provider, live schedule, production database, alert destination, or remote route acceptance procedure.
- [ ] **Step 3: Generate the development evidence JSON** from the exact candidate and run the package checker against it. Expected: development tests have their own scope; all external rows remain `NOT RUN`/`HELD`; aggregate external disposition remains `NO_GO`.
- [ ] **Step 4: Have an independent reviewer inspect** migration continuity/downgrades, route UUID/digest binding, one-use no-effect reissue, complete rollback inventory, fixture guards, evidence scope, and gate status. Record findings and fixes in this ledger.
- [ ] **Step 5: Commit** exact-candidate evidence and the final report. Do not push or merge as part of this plan.

## Requirements Trace

Every numbered acceptance item in the four updated specs maps to a development task. “External `NOT RUN`/`HELD`” means the local contract/fake test may run, but the named-target procedure is outside this plan because no production target or owners are present.

### Slice 2 policy/revocation baseline

| Spec acceptance case | Owning task | Trace |
|---|---:|---|
| Stale authority snapshot | 8, 11 | Preserve current subject-revision denial and verify the full current policy suite. |
| Missing authority | 8, 11 | Preserve fail-closed primary reads. |
| Revoked grant | 8, 11 | Preserve revocation/regrant behavior. |
| Revoked policy | 8, 11 | Preserve terminal policy/approval identity. |
| Expired authority | 8, 11 | Preserve database-time expiry checks. |
| Wrong scope/workspace | 8, 11 | Preserve exact operation/provider/account binding. |
| Stop wins concurrent claim | 8, 11 | Preserve transaction ordering and denial events. |
| Claim wins concurrent stop | 8, 11 | Preserve one in-flight liability and deny every later action. |
| Duplicate workers | 4, 5, 8, 11 | Route owner and permit claim both serialize. |
| Primary outage | 8, 11 | Preserve outage denial and original liability. |
| Crash before claim | 5, 8, 11 | Restart reloads current authority, route, and permit. |
| Crash after claim/acceptance | 7, 8, 9, 11 | Preserve exact operation; recovery/rollback records remain complete. |
| Revocation propagation | 4, 5, 7, 8, 11 | Every later route/action/read checks current authority. |
| Production permit storage and mode separation | 2, 8, 11 | Add only additive production records; fixture relations stay isolated. |
| Route fence at every action boundary | 4, 5, 8, 11 | Local fake proves contract only; real platform ordering remains external `NOT RUN`/`HELD`. |
| Immutable decision evidence | 2, 7, 8, 11 | Preserve migrations 0044–0048 and verify immutable history; Task 7's 0049 is additive evidence validation. |
| No implicit provider substitution | 7, 8, 11 | Exact target/read grant required; no write or alternate provider. |

### Production routing and worker fencing

| Spec acceptance item | Owning task | External result |
|---|---:|---|
| 1. Exact route admission | 1, 5, 11 | Target route acceptance `NOT RUN`/`HELD`. |
| 2. Artifact and floor denials | 5, 11 | Exact deployed artifact check `NOT RUN`/`HELD`; 0043 remains fixture-only. |
| 3. Lease ownership and stale fencing | 4, 5, 11 | External lease service/real process pause `NOT RUN`/`HELD`. |
| 4. Restart identity | 5, 11 | Local fake replay test; named Temporal history readback `NOT RUN`/`HELD`. |
| 5. Drain and readback | 5, 9, 11 | Real route/poller/lease readback `NOT RUN`/`HELD`. |
| 6. Rollback and unchanged liabilities | 2, 8, 9, 11 | Complete local inventory tests; target rollback `NOT RUN`/`HELD`. |
| 7. Named-environment run | 10, 11 | External-only `NOT RUN`/`HELD`. |

### Live schedule fencing

| Spec acceptance test | Owning task | External result |
|---|---:|---|
| `test_live_inventory_requires_complete_target_scoped_two_sided_binding` | 3, 6 | Live target inventory `NOT RUN`/`HELD`. |
| `test_production_binding_does_not_relax_fixture_target_guards` | 3, 6, 11 | Local regression. |
| `test_pending_requires_remote_pause_readback_and_drained_actions` | 4, 6 | Remote target pause/drain `NOT RUN`/`HELD`. |
| `test_lost_pause_ack_or_source_readback_reconciles_one_exact_schedule` | 6 | Live timeout/readback `NOT RUN`/`HELD`. |
| `test_manual_event_and_scheduled_same_slot_coalesce_after_restart` | 6, 11 | Local database/Temporal fake. |
| `test_stop_or_revocation_racing_activation_and_poll_fails_closed` | 6, 8 | Live policy/schedule exercise `NOT RUN`/`HELD`. |
| `test_out_of_band_unpause_or_action_route_drift_holds_and_alerts` | 6, 7 | External alert and target readback `NOT RUN`/`HELD`. |
| `test_crash_at_each_cutover_boundary_recovers_from_durable_state` | 6, 11 | Local fake crash suite. |
| `test_unresolved_v4_work_or_remote_liability_blocks_rollback` | 6, 7, 9 | Live liability/readback `NOT RUN`/`HELD`. |
| `test_rollback_requires_later_slot_readback_before_prior_route_restart` | 6, 9 | Live resume/route readback `NOT RUN`/`HELD`. |
| `test_candidate_and_environment_mismatch_never_passes_live_acceptance` | 10, 11 | Checker local; named environment absent, `NOT RUN`/`HELD`. |
| `test_schedule_controller_has_no_effect_or_schedule_creation_authority` | 6, 11 | Local fake API surface; target IAM proof `NOT RUN`/`HELD`. |

### Production recovery, remote read, and alerts

| Spec acceptance item | Owning task | External result |
|---|---:|---|
| 1. Exact source and stable work identity | 2, 7, 11 | Local migration/fake. |
| 2. Original operation read scope | 7, 11 | Local fake proves no write/broad-read API; provider IAM proof `NOT RUN`/`HELD`. |
| 3. Authority and stop races | 4, 7, 8, 11 | Local fake races; target propagation `NOT RUN`/`HELD`. |
| 4. Result classification | 7, 8, 11 | Local fake; provider consistency proof `NOT RUN`/`HELD`. |
| 5. Crash and exact replay | 2, 7, 11 | Local fake. |
| 6. Production case-command and deadline controller | 2, 7, 8, 11 | Local database/fake; named command owner `NOT RUN`/`HELD`. |
| 7. Alert delivery and acknowledgment | 2, 7, 10, 11 | Fake delivery local; external channel/recipient `NOT RUN`/`HELD`. |
| 8. Owner and outage escalation | 2, 7, 10, 11 | Fake outage local; on-call roster `NOT RUN`/`HELD`. |
| 9. Two-reason concurrent hold insert and crash | 2, 7, 9, 11 | Local database/fake; no provider effects. |
| 10. Complete rollback inventory before prior-code restart | 9, 11 | Local inventory tests; target prior-code restart `NOT RUN`/`HELD`. |
| 11. Operationless intent restart and concurrency | 2, 7, 11 | Local database/fake; target wake service `NOT RUN`/`HELD`. |
| 12. Schema and migration safety | 2, 7, 8, 9, 11 | Local migration tests cover 0047–0051, safe 0049/0050 downgrades, and 0051 fail-before-mutation. |
| 13. Exact candidate/environment evidence | 10, 11 | Development evidence local; named target and owners `NOT RUN`/`HELD`. |

### External RG0/RG1 acceptance package/checker

All twenty tests validate the local package/checker contract in Task 10. Passing them cannot satisfy external row procedures, which remain `NOT RUN`/`HELD` until the target-specific prerequisites exist.

| Spec acceptance test | Owning task |
|---|---:|
| `test_package_requires_exact_candidate_tree_artifacts_deployment_and_environment` | 10, 11 |
| `test_package_requires_exact_row_set_once` | 10 |
| `test_each_row_requires_named_authorized_owner_operator_and_independent_reviewer` | 10 |
| `test_procedure_is_pinned_and_executable` | 10 |
| `test_raw_results_and_rollback_are_hashed_and_referenced` | 10 |
| `test_scope_transfer_across_fixture_ci_commit_environment_account_is_rejected` | 10 |
| `test_ci_formal_approval_and_external_acceptance_remain_distinct` | 10 |
| `test_fail_not_run_held_stale_or_scope_mismatch_blocks_package` | 10 |
| `test_missing_or_unapproved_freshness_limit_holds_mutable_evidence` | 10 |
| `test_planned_revocation_with_verified_final_denial_passes` | 10 |
| `test_unplanned_or_subsequent_revision_change_fails` | 10 |
| `test_no_mutation_requires_equal_start_and_end_state` | 10 |
| `test_revoked_credentials_are_never_restored` | 10 |
| `test_run_invalidation_is_append_only_and_transitive` | 10 |
| `test_negative_controls_pass_only_on_proven_denial_and_zero_effect` | 10 |
| `test_package_cannot_enable_production_or_release_a_gate` | 10 |
| `test_final_audit_is_separate_and_rechecks_every_row` | 10 |
| `test_rollback_requires_pre_state_action_and_exact_post_state_readback` | 10 |
| `test_observability_artifacts_are_correlated_and_secret_free` | 10 |
| `test_default_package_adds_no_application_migration_and_excludes_later_phases` | 10 |

## External Prerequisites That Remain Outside This Plan

The development plan does not provide a named production deployment/account/database/Temporal namespace, the external linearizable route-lease implementation, worker and schedule owners, source/business owners, named policy/recovery/alert/on-call owners, least-privilege credentials, a provider read target and consistency guarantee, an external alert channel/recipient roster, approved freshness/retry/deadline policy, signed production artifacts, current repository-enforcement readback, or independent production reviewer. These are required for the later target-specific acceptance run. Until then, production effects stay disabled; all five RG0 checks and all 17 RG1 rows stay `NOT RUN`/`HELD`; RG0/RG1 remain `HELD`; P2 remains `BLOCKED`; `main` remains unmerged.
