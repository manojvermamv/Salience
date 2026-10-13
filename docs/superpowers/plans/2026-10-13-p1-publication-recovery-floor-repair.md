# P1 Publication Recovery Floor Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make the publication-recovery hold floor durable and independently verifiable across SQL, replay, worker startup, installed-image migration, and cross-container rehearsal. A worker may register only against an authenticated schema at revision 0043 or an individually qualified descendant, and every retained hold must have a complete v1 liability snapshot.

**Architecture:** migration 0043 adds nullable v1 evidence alongside the immutable 0042 hold contract. Database insertion, application recording/replay, offline verification, authenticated migration inventory, and the isolated launcher enforce the same exact 21-key projection. Qualification runs both retained hold reasons against a rebuilt read-only image and proves the adapter operation recorder remains empty.

**Tech Stack:** PostgreSQL and Alembic; Python 3.13; pytest; Docker Compose; GitHub Actions; existing V4 publication-recovery and documentation checkers.

**Spec:** [2026-10-13 P1 Publication Recovery Floor Repair Design](../specs/2026-10-13-p1-publication-recovery-floor-repair-design.md) — docs/superpowers/specs/2026-10-13-p1-publication-recovery-floor-repair-design.md

## Global Constraints

- Preserve migration 0042 byte-for-byte and retain its existing columns, trigger, role, grants, and behavior. Add migration 0043 only; never edit, squash, or rewrite 0042.
- Use the exact ordered v1 key list from the spec: dispatch, context, cycle, job, permit_claim, permit, fixture_receipt, fixture_progress, publication_request, publication_plan, publication_attempt, external_effect, budget_reservation, output, remote_publication_receipts, publication_status_events, cycle_events, cycle_outbox, job_checkpoints, audit_events, provenance_records. Keep key membership separate from digest verification.
- Preserve the old 16-key comparison as a diagnostic in tests, while storing and replaying the full v1 snapshot. SQL NULL output must become JSON null; every collection must be a JSON array, including empty arrays. Match the spec's filters, ordering, and PostgreSQL JSONB canonical digest exactly.
- Insert the old and new hold evidence atomically in one INSERT and one transaction. Never infer missing v1 evidence from current rows. A legacy row with NULL v1 fields remains inspectable and immutable but is not replayable, verifiable, or restartable.
- Keep both authoritative reasons: expired_permit_receipt and published_during_cancel. Preserve existing concurrent-insert and crash-after-commit fixtures; extend them to assert one stable v1 hold and unchanged complete liabilities.
- Before any prior worker code can restart during rollback rehearsal, run the rollback verifier for both reasons and prove the complete liabilities remain unchanged. A populated 0043 downgrade must fail closed without changing rows; it does not authorize restarting old code on 0042.
- The minimum accepted database floor is 0043. Reject 0042 even when empty, plus earlier, branched, ambiguous, missing, extra, modified, symlinked, or unauthenticated migration state before worker registration. Accept only 0043 or an individually qualified descendant.
- The supported current-worker command is /usr/local/bin/python -I -B -S /run/salience-control/launch-v4-publication-worker.py --mode current --worker-kind deployable. The fixture rehearsal uses the same flags and launcher path with --worker-kind legacy-fixture. Runtime code is installed at /usr/local/lib/python3.13/site-packages, authenticated before import, and read-only through worker execution.
- Keep production/external effects disabled, fixture budget at 0, RG0 and full RG1 HELD, P2–P7 gated, P6 disabled, and main unmerged. AgentTube is immutable and reference-only; do not edit it.
- Build evidence only from the exact candidate artifact. Any code or artifact change after qualification invalidates artifact evidence and requires a rebuild and rehearsal. Any code, artifact, documentation, or test change after final review invalidates affected review and verification; rerun those checks against the exact candidate.
- Never rewrite historical evidence, transfer old test counts, or claim that 5cb5791 was supported. The M1 digest fix is an additive report-only correction: prior report-only digest 7f06f404226b346873755d76c6cffdcc7ddfb7ecad79d5de57f770b44010ebcf; correct exact committed bytes digest 65cb0ac34344f7d8931ceec32b214797c05e73a82dba4b64108184c9e9394836.

## Review Focus

1. **Legacy evidence denial:** can an existing 0042 hold with NULL v1 fields be inspected while replay, offline verification, and actual rollback-restart launch all deny it before registration without changing liabilities? Pin: tests/integration/test_v4_publication_recovery_floor.py::test_rollback_restart_launcher_denies_legacy_null_v1_before_registration.
2. **Omitted-collection integrity:** does an independent mutation to each omitted collection change the v1 digest and deny replay, verification, and actual rollback-restart launch while the original 16-key projection remains unchanged? Pin: tests/integration/test_v4_publication_recovery_floor.py::test_rollback_restart_launcher_denies_omitted_collection_mutation_before_registration.
3. **Startup isolation:** do hostile import environment, working directory, and a valid timestamp/size malicious .pyc fail through the real supported launcher, with a clean read-only positive control and no cache writes? Pin: tests/integration/test_v4_publication_recovery_floor.py::test_supported_launcher_rejects_hostile_startup_and_pyc_before_registration.
4. **Authenticated migration inventory:** do exact-set and digest checks reject an extra valid descendant, a removed revision, and modified revision metadata before parsing or DB access? Pin: tests/integration/test_v4_publication_recovery_floor.py::test_authenticated_migration_inventory_rejects_unexpected_state[extra-valid-descendant].
5. **Cross-container effect boundary:** does a method- and platform-neutral recorder observe zero adapter operations for both held reasons across the actual worker and recorder containers, while a positive control proves the recorder works? Pin: tests/integration/test_v4_publication_recovery_floor.py::test_cross_container_adapter_recorder_observes_zero_operations_for_both_holds.

## File Responsibilities

- migrations/versions/0043_complete_publication_recovery_liabilities.py: additive schema, named constraints, SECURITY DEFINER insert guard, privileges, and fail-closed populated downgrade. Leave 0042 untouched.
- src/salience/cycles/publication_recovery.py: exact 21-key record/replay/verifier projection, canonical digest, atomic evidence insertion, and legacy NULL-v1 denial.
- src/salience/cycles/publication_recovery_compatibility.py: hard 0043 floor and authenticated exact migration inventory using lock source_files; verified bytes only.
- scripts/launch-v4-publication-worker.py: stdlib-only -I -B -S preflight and child bootstrap; authenticate before imports, reject any .pyc, and enforce a read-only runtime root through exec.
- Dockerfile, compose.yaml, alembic.ini, migrations/__init__.py, migrations/env.py, and migration package initializers: wheel-installed Alembic support and exact config path. Set both Dockerfile CMD and Compose worker command to start the external launcher with -I -B -S; set the Compose worker `read_only: true`. Set `PUBLICATION_WORKER_LAUNCHER_PATH` to `./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/launch-v4-publication-worker.py`, `PUBLICATION_WORKER_LOCK_PATH` to `./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.json`, and `PUBLICATION_WORKER_LOCK_DIGEST_PATH` to `./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.sha256`; mount each read-only at its matching fixed `/run/salience-control/` path. Keep migrations and launcher assets in the authenticated image.
- tests/integration/test_v4_migrations.py, tests/integration/test_v4_publication_recovery_holds.py, and tests/integration/test_v4_publication_recovery_floor.py: extend existing migration, hold, launcher, inventory, and image seams. Reuse current fixtures and existing race/crash tests; add only the missing assertions and focused negative controls.
- tests/fixtures/v4_publication_floor_worker.py: actual fixture container startup, isolated launcher argv, drain/stop, and denial-probe helpers.
- tests/fixtures/v4_publication_adapter_operation_recorder.py and tests/fixtures/v4_publication_adapter_operation_receiver.py: method- and platform-neutral cross-container operation wrapper and receiver. Record the capabilities property read and exactly PublisherAdapter methods preflight, prepare_delivery, create_or_resume, submit, reconcile, status, cancel, verify_webhook, and refresh_capabilities. Avoid provider-specific branches or payload/credential logging.
- deployment/v4/publication-worker-launch-lock.json: tracked canonical lock. Generate its independent digest at artifacts/v4-next-p1/agents/p1-publication-recovery-0043/publication-worker-launch-lock.sha256; write the read-only external control bundle at artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/launch-v4-publication-worker.py, artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.json, and artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.sha256. These artifacts are ignored, not commit content. The container receives those three control files at /run/salience-control/.
- Create: scripts/build_publication_worker_launch_lock.py: deterministic final candidate pin builder. It consumes the exact wheel and installed runtime tree, emits canonical lock bytes, the independent SHA-256 file, and the byte-identical external control bundle; its CLI and invocation are specified in Tasks 3 and 8.
- docs/superpowers/specs/2026-10-13-p1-publication-recovery-floor-repair-design.md: Task 0 may correct only the stale review-status line; preserve all approved design content. The historical plan, historical evidence, and AgentTube files remain read-only.
- docs/v4/progress.md, docs/v4/IMPLEMENTATION-BLUEPRINT.md, docs/v4/p0-release.json, docs/v4/p1-qualification-matrix.json, docs/v4/publication-recovery-hold-evidence.json, docs/v4/documentation-status-audit.json, and docs/v4/evidence-lock.json: Task 0 and Task 8 correct only current status and next actions, preserving old evidence and counts.
- .superpowers/sdd/2026-10-13-p1-publication-recovery-floor-repair/implementation-report.md: ignored implementation/rehearsal report and dependent-reference reconciliation. planning-self-review.md remains the plan-only review; do not alter archived evidence.
- artifacts/v4-next-p1/agents/p1-publication-recovery-0043/m1-report-digest-correction.json: ignored report-only M1 digest correction. It is not commit content; tracked status/evidence summaries may reference its path and digest without implying it is tracked.

## Tasks

### Task 0 — Reconcile stale Task 11, spec, and handoff status

**Files:** docs/v4/progress.md lines 7, 18, 24, 27, and 86–88; docs/v4/IMPLEMENTATION-BLUEPRINT.md lines 5, 11, and 35–37; docs/v4/p0-release.json lines 468–469; docs/v4/p1-qualification-matrix.json lines 492–495 and 578–581; docs/v4/publication-recovery-hold-evidence.json lines 1206–1213; docs/v4/documentation-status-audit.json lines 1425–1431; docs/v4/evidence-lock.json lines 1446–1447; docs/superpowers/specs/2026-10-13-p1-publication-recovery-floor-repair-design.md review-status line 4; ignored .superpowers/sdd/2026-10-13-p1-publication-recovery-floor-repair/implementation-report.md.

**Interfaces:** consume the approved repair spec and audit assertions; produce status-only edits to the listed handoffs plus the ignored implementation report. No runtime API is added. Later tasks consume the recorded state: design approved, 5cb5791 unsupported, final rereview/requalification pending, and earlier counts/results not transferred.

- [ ] Read each named status field and its source record; preserve historical review records and test counts verbatim.
- [ ] First record the current-state assertions in .superpowers/sdd/2026-10-13-p1-publication-recovery-floor-repair/implementation-report.md: the 5cb5791 review found floor/evidence gaps; the repair design is approved; final corrected-candidate review and requalification remain open; corrected-publication rereview evidence covers only A4 a4b2368... and docs/observed head d031172...; it does not cover 7878208 or 5cb5791.
- [ ] Make those assertions fail before changing handoffs: run python -m json.tool on the named JSON files and compare their current next-action/status fields with the approved repair state.
- [ ] Update stale Task 11/spec wording and handoffs to say the old candidate is not supported, the approved repair is pending implementation, final review/requalification remain open, and no earlier count/result transfers. Do not mark the floor fixed or qualification complete.
- [ ] Re-run JSON parsing, git diff --check, and compare the ignored report against every changed status field. Keep final next-action fields for Task 8 until final evidence is produced.
- [ ] Review the diff for historical evidence/count changes; remove any such change before completion. Commit this reconciliation as one reviewable status-only commit.

### Task 1 — Add migration 0043 and populated-database rollback boundaries

**Files:** new migrations/versions/0043_complete_publication_recovery_liabilities.py; tests/integration/test_v4_migrations.py; tests/integration/test_v4_publication_recovery_holds.py.

**Interfaces:** Alembic calls revision upgrade() after 0042 and downgrade() before 0042. The SQL trigger function public.v4_guard_legacy_publication_recovery_hold_v1() accepts an INSERT only when all v1 fields and the current canonical projection agree; otherwise PostgreSQL raises an error and inserts no row. Application code consumes the three new nullable columns and named constraints without changing the 0042 interface.

- [ ] Add failing tests first for upgrading a populated 0042 database without changing existing rows, columns, trigger, fixture role, NOLOGIN, schema USAGE, table SELECT/INSERT-only grants, PUBLIC denial, and update/delete immutability.
- [ ] Add failing SQL tests in tests/integration/test_v4_migrations.py: test_0043_upgrade_preserves_populated_0042_rows_and_contract; parameterized test_0043_insert_guard_rejects_invalid_v1_projection with exact case IDs missing-key, extra-key, bad-tag, bad-fingerprint, current-row-mismatch, and null-v1; test_0043_downgrade_refuses_populated_v1_without_mutation; and test_0043_downgrade_without_v1_preserves_legacy_rows. The parameterized insert test must perform a real INSERT, assert PostgreSQL rejection, and assert no hold row was added for each of the six cases.
- [ ] Also assert the three nullable v1 columns and exact constraints: ck_v4_legacy_publication_recovery_holds_v1_all_or_none, ck_v4_legacy_publication_recovery_holds_v1_version, ck_v4_legacy_publication_recovery_holds_v1_snapshot_keys, and ck_v4_legacy_publication_recovery_holds_v1_fingerprint. Check that the 0043 SECURITY DEFINER function has fixed search_path pg_catalog, public and its before-insert trigger validates the exact projection and canonical digest.
- [ ] Add failing downgrade tests for both branches: with any non-NULL v1 field, downgrade takes a 3-second lock timeout, refuses under SHARE ROW EXCLUSIVE, and changes nothing; with only legacy 0042 rows, it drops only 0043 objects/columns and preserves all 0042 rows. Keep 0042 populated downgrade refusal covered.
- [ ] Run the focused tests and confirm they fail for missing revision/behavior:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_migrations.py tests/integration/test_v4_publication_recovery_holds.py -k '0043_upgrade_preserves_populated_0042_rows_and_contract or 0043_insert_guard_rejects_invalid_v1_projection or 0043_downgrade_'
~~~
- [ ] Implement 0043 only. Add no data update, delete, backfill, or new role. Preserve all 0042 objects and behavior.
- [ ] Re-run the focused tests and confirm green. Inspect the revision to verify down_revision is exactly 0042 and all downgrade refusal checks precede destructive DDL.
- [ ] Commit this migration and its tests as one reviewable commit.

### Task 2 — Store, replay, and verify exact v1 liability evidence

**Files:** src/salience/cycles/publication_recovery.py; tests/integration/test_v4_publication_recovery_holds.py; migration guard tests in tests/integration/test_v4_migrations.py.

**Interfaces:** retain record_publication_recovery_hold(database_url, *, workspace_id: UUID, operation_id: UUID, reason: PublicationRecoveryReason) -> PublicationRecoveryHoldView and verify_publication_recovery_rollback(database_url, *, workspace_id: UUID, operation_ids: Mapping[PublicationRecoveryReason, UUID]) -> tuple[PublicationRecoveryHoldView, PublicationRecoveryHoldView]. Both consume/produce the authoritative reason keys expired_permit_receipt and published_during_cancel. Keep PublicationRecoveryConflictError as the denial type for conflicting or NULL-v1 replay; the verifier remains read-only.

- [ ] Add failing tests named test_v1_snapshot_has_exact_ordered_21_key_projection, test_v1_collection_filters_and_ordering_match_spec parameterized with exact case IDs remote-publication-receipts, publication-status-events, cycle-events, cycle-outbox, job-checkpoints, audit-events, and provenance-records; test_record_replay_verifier_share_atomic_v1_snapshot; and test_legacy_null_v1_hold_is_inspectable_but_not_replayable_or_verifiable. Assert JSON-null output, empty-array normalization, canonical PostgreSQL JSONB fingerprint, one INSERT/transaction, stable hold ID, and byte-for-byte unchanged old 16-key diagnostic projection.
- [ ] In test_legacy_null_v1_hold_is_inspectable_but_not_replayable_or_verifiable, seed a legacy 0042 row with all three v1 fields NULL. Assert inspection succeeds but replay raises PublicationRecoveryConflictError, offline verification rejects the row, and liabilities remain unchanged. No fallback projection or inferred baseline is allowed.
- [ ] Run the focused tests and confirm they fail before implementation:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_holds.py tests/integration/test_v4_migrations.py -k 'v1_snapshot_has_exact_ordered_21_key_projection or v1_collection_filters_and_ordering_match_spec or record_replay_verifier_share_atomic_v1_snapshot or legacy_null_v1_hold_is_inspectable_but_not_replayable_or_verifiable'
~~~
- [ ] Implement one shared exact projection contract for SQL insert guard, record, replay, and offline verifier. Preserve existing ordering and the old 16-key diagnostic; add the spec's four omitted collections and complete output handling to v1.
- [ ] Keep the verifier read-only and repeatable-read with the 3-second lock timeout. Verify exact distinct reasons and workspace scope.
- [ ] Re-run the focused tests and confirm green. Commit implementation and tests as one reviewable commit.

### Task 3 — Enforce the hard 0043 floor and authenticated exact migration inventory

**Files:** src/salience/cycles/publication_recovery_compatibility.py; tests/integration/test_v4_publication_recovery_floor.py; deployment/v4/publication-worker-launch-lock.json; artifacts/v4-next-p1/agents/p1-publication-recovery-0043/publication-worker-launch-lock.sha256; Create scripts/build_publication_worker_launch_lock.py.

**Interfaces:** require_publication_worker_compatibility(database_url, *, start_mode, launch_lock, observed_launch_lock_sha256, rollback_workspace_id=None, rollback_operation_ids=None) -> PublicationWorkerPin | None returns only a qualified pin or raises PublicationWorkerCompatibilityError before worker registration. PublicationWorkerLaunchLock.pin.source_files is the authenticated expected file inventory. `scripts/build_publication_worker_launch_lock.py` exposes `build_publication_worker_launch_lock(*, application_commit: str, wheel: Path, runtime_root: Path, lock_path: Path, independent_digest_path: Path, control_dir: Path) -> PublicationWorkerLaunchLock`; its CLI accepts --application-commit, --wheel, --runtime-root, --lock, --independent-digest, and --control-dir and emits canonical lock bytes plus the independent digest/control bundle.

- [ ] Add failing tests named test_worker_floor_rejects_empty_0042_before_registration; parameterized test_worker_floor_rejects_unknown_pre_floor_and_ambiguous_lineages_before_registration with exact IDs earlier-revision, unknown-revision, branched-history, and ambiguous-head; and parameterized test_worker_floor_accepts_0043_and_qualified_descendant with IDs exact-0043 and qualified-descendant. Assert denial occurs before worker registration.
- [ ] Add parameterized test_authenticated_migration_inventory_rejects_unexpected_state with exact IDs extra-valid-descendant, missing-revision, modified-revision-hash, modified-down-revision, symlink-path-escape, and independent-digest-mismatch; add test_authenticated_migration_inventory_accepts_exact_0043_inventory and parameterized test_authenticated_migration_graph_rejects_branch_cycle_or_ambiguous_head with IDs branch, cycle, and ambiguous-head. Each denial must precede revision metadata parsing/DB access as applicable.
- [ ] Add failing test_publication_worker_launch_lock_builder_emits_canonical_lock_digest_and_identical_control_bundle. With synthetic wheel, installed RECORD/runtime tree, launcher, and pinned source inputs, assert lock bytes equal UTF-8 `json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n"`, the independent digest file is exactly lowercase `sha256(lock_bytes) + "\n"`, and tracked lock/control lock, launcher/control launcher, and independent/control digest are byte-identical.
- [ ] Run the focused tests and confirm they fail against the current permissive graph scan:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_floor.py -k 'worker_floor_rejects or worker_floor_accepts or authenticated_migration_inventory or authenticated_migration_graph'
~~~
- [ ] Run the builder regression alone and confirm RED before creating its implementation:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_floor.py::test_publication_worker_launch_lock_builder_emits_canonical_lock_digest_and_identical_control_bundle
~~~
- [ ] Implement exact inventory verification from authenticated lock source_files. The expected inventory includes alembic.ini, migrations/__init__.py, migrations/env.py, migrations/versions/__init__.py, every revision from migrations/versions/0001 through migrations/versions/0043, the launcher and all loaded support files. Require equality with the exact regular .py revision set and pin all listed non-.py support files too; reject extras, omissions, symlinks, path escapes, or changed bytes before parsing revision metadata.
- [ ] Implement `build_publication_worker_launch_lock(...)` and the script CLI in scripts/build_publication_worker_launch_lock.py. Serialize canonical lock JSON deterministically, write the independent SHA-256 digest, and emit byte-identical launcher, lock, and digest files into the external `control_dir`; fail if the tracked source, wheel, installed RECORD, or runtime inventory does not match `application_commit`.
- [ ] Re-run test_publication_worker_launch_lock_builder_emits_canonical_lock_digest_and_identical_control_bundle and confirm GREEN before committing the gate, builder, and focused tests together.
- [ ] For these tests, generate a synthetic authenticated lock containing that full expected inventory. After Tasks 1–7 have finalized source code and the installed wheel, Task 8 regenerates the tracked production lock and independent digest from the exact wheel/runtime and copies the independent control bundle. Run exact-candidate inventory tests and the actual image rehearsal only after those final pins match the built artifact; if any pinned input changes, regenerate pins and repeat.
- [ ] Parse verified bytes only. Require one root and head, unique revision IDs, one parent per revision, present parents, no cycles, exact 0042→0043 chain, and floor 0043. Ensure launcher authentication completes before graph access or DB registration.
- [ ] Re-run the focused tests and confirm green. Commit the gate and tests as one reviewable commit.

### Task 4 — Isolate the real launcher and reject bytecode before registration

**Files:** scripts/launch-v4-publication-worker.py; Dockerfile; compose.yaml; tests/integration/test_v4_publication_recovery_floor.py; tests/fixtures/v4_publication_floor_worker.py.

**Interfaces:** the external launcher CLI accepts --mode current or --mode rollback-restart, --worker-kind deployable or legacy-fixture, and for rollback exactly --workspace-id, --expired-hold-operation-id, and --cancel-hold-operation-id. The exact Dockerfile CMD and Compose worker command are ["/usr/local/bin/python", "-I", "-B", "-S", "/run/salience-control/launch-v4-publication-worker.py", "--mode", "current", "--worker-kind", "deployable"]. Compose sets worker `read_only: true` and uses these exact sources/destinations with read-only mounts: `PUBLICATION_WORKER_LAUNCHER_PATH=./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/launch-v4-publication-worker.py` to `/run/salience-control/launch-v4-publication-worker.py:ro`; `PUBLICATION_WORKER_LOCK_PATH=./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.json` to `/run/salience-control/publication-worker-launch-lock.json:ro`; and `PUBLICATION_WORKER_LOCK_DIGEST_PATH=./artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.sha256` to `/run/salience-control/publication-worker-launch-lock.sha256:ro`. start_rollback_worker(...) builds Docker argv with `--read-only` before the image and the container argv ["/usr/local/bin/python", "-I", "-B", "-S", CONTROL_LAUNCHER, "--mode", "rollback-restart", "--worker-kind", "legacy-fixture", ...]; invoke_launcher(..., argv: tuple[str, ...]) prefixes that same interpreter/flag/launcher triple. Compatibility preflight must return success before execve; denial exits 78 without worker registration. No writable mount covers the runtime root or control bundle; add an explicit `/tmp` tmpfs only if a test proves it is required.

- [ ] Extend test_docker_and_compose_worker_use_pinned_wheel_and_trusted_launcher and add test_dockerfile_and_compose_worker_command_use_isolated_launcher_flags to parse Dockerfile CMD and compose.yaml worker command, asserting the full argv equals ["/usr/local/bin/python", "-I", "-B", "-S", "/run/salience-control/launch-v4-publication-worker.py", "--mode", "current", "--worker-kind", "deployable"]. Add test_compose_worker_mounts_generated_candidate_control_bundle_read_only to assert all three path variables default under `artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/`, map to the exact fixed destinations, and mount read-only; assert the Compose worker has `read_only: true`. Add test_start_rollback_worker_argv_uses_isolated_launcher_flags and test_invoke_launcher_argv_uses_isolated_launcher_flags for the full Docker-run/subprocess argv, asserting `--read-only` before the image, isolated interpreter flags, exact read-only control mounts, and rollback args.
- [ ] Add failing subprocess test test_supported_launcher_authenticates_artifact_lock_interpreter_and_runtime_root_before_registration with exact IDs artifact-digest-mismatch, lock-digest-mismatch, launcher-hash-mismatch, interpreter-hash-mismatch, and runtime-root-mismatch; assert each real supported launcher process denies before application import or worker registration.
- [ ] Add failing subprocess tests named test_supported_launcher_rejects_hostile_startup_and_pyc_before_registration with hostile-pythonpath, hostile-pythonhome-sitecustomize, hostile-working-directory, and valid-pyc cases; assert the real image launcher rejects each before application import or worker registration.
- [ ] Add test_supported_launcher_read_only_root_creates_no_pyc with a clean positive control and a read-only runtime root.
- [ ] Add real-container rollback-restart negative tests test_rollback_restart_launcher_denies_omitted_collection_mutation_before_registration, parameterized by remote-publication-receipts, job-checkpoints, audit-events, and provenance-records, and test_rollback_restart_launcher_denies_legacy_null_v1_before_registration. Each must use invoke_launcher(..., argv=...) against the actual supported launcher, assert exit 78 before worker registration, and assert the verifier caused no hold/liability mutation.
- [ ] Run the focused subprocess tests and confirm they fail with current -I -c preflight and -I -m child startup:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_floor.py -k 'docker_and_compose_worker_use_pinned_wheel_and_trusted_launcher or dockerfile_and_compose_worker_command_use_isolated_launcher_flags or compose_worker_mounts_generated_candidate_control_bundle_read_only or start_rollback_worker_argv_uses_isolated_launcher_flags or invoke_launcher_argv_uses_isolated_launcher_flags or supported_launcher_authenticates_artifact_lock_interpreter_and_runtime_root_before_registration or supported_launcher_rejects_hostile_startup_and_pyc_before_registration or supported_launcher_read_only_root_creates_no_pyc or rollback_restart_launcher_denies_omitted_collection_mutation_before_registration or rollback_restart_launcher_denies_legacy_null_v1_before_registration'
~~~
- [ ] Change preflight and child execution to the exact isolated -I -B -S invocation. Use a stdlib-only bootstrap; authenticate interpreter, launcher, trust root, lock, wheel/RECORD, all source files, manifest, import paths, entrypoint, and runtime root before adding only the fixed trusted root to sys.path.
- [ ] Set Dockerfile CMD and compose.yaml worker command exactly to ["/usr/local/bin/python", "-I", "-B", "-S", "/run/salience-control/launch-v4-publication-worker.py", "--mode", "current", "--worker-kind", "deployable"]. Set Compose worker `read_only: true`; update its launcher, lock, and digest mount variables so all three sources are the matching generated control-bundle files and each destination uses `:ro`. Update start_rollback_worker argv to include `--read-only`, interpreter, -I -B -S, launcher, then rollback-restart/legacy-fixture arguments; update invoke_launcher the same way.
- [ ] Scan the entire authenticated runtime root for .pyc, including symlinks, before application imports. Keep the root read-only through execution; do not rely on -B alone. If `/tmp` needs to be writable, mount only an explicit tmpfs there and prove it does not shadow the runtime root/control bundle.
- [ ] Verify both supported invocations with exact flags:
~~~
/usr/local/bin/python -I -B -S /run/salience-control/launch-v4-publication-worker.py --mode current --worker-kind deployable
/usr/local/bin/python -I -B -S /run/salience-control/launch-v4-publication-worker.py --mode current --worker-kind legacy-fixture
~~~
- [ ] Re-run the subprocess tests and confirm green. Commit launcher changes and focused tests as one reviewable commit.

### Task 5 — Install Alembic configuration with the wheel and migrate a real disposable database

**Files:** Dockerfile; compose.yaml; alembic.ini; migrations/__init__.py; migrations/env.py; migrations/versions/__init__.py; tests/integration/test_v4_migrations.py; tests/integration/test_v4_publication_recovery_floor.py.

**Interfaces:** the built worker image contains the retained wheel at /opt/salience-artifacts/salience.whl and the Alembic environment under /usr/local/lib/python3.13/site-packages/migrations. The installed command is exactly alembic -c /usr/local/lib/python3.13/site-packages/alembic.ini upgrade head and operates on the DATABASE_URL for a newly created disposable PostgreSQL database; successful completion leaves one version row at 0043.

- [ ] Add failing test_installed_alembic_config_resolves_inside_runtime_root and test_installed_image_migrates_disposable_database_to_0043. The latter must build the wheel-only image, create a fresh disposable PostgreSQL database, run the installed-image command, and query exact head 0043, role/privilege state, trigger, constraints, and absence of fixture rows; do not substitute source-tree Alembic or an in-process upgrade.
- [ ] Run focused image/migration tests and confirm red:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_migrations.py tests/integration/test_v4_publication_recovery_floor.py -k 'installed_alembic_config_resolves_inside_runtime_root or installed_image_migrates_disposable_database_to_0043'
~~~
- [ ] Package Alembic config and all migration support inside the installed runtime root. Set script_location = %(here)s/migrations. Update the fixture/deploy migration command to:
~~~
alembic -c /usr/local/lib/python3.13/site-packages/alembic.ini upgrade head
~~~
- [ ] Rebuild the worker image from the retained wheel, run the exact command against a newly created disposable DB, and assert database head 0043 and schema/grants. Ensure migration support is in the authenticated source inventory.
- [ ] Re-run the focused image/migration tests and confirm green. Commit image/config/test changes as one reviewable commit.

### Task 6 — Extend both-reason races, crash replay, and omitted-collection mutation checks

**Files:** tests/integration/test_v4_publication_recovery_holds.py; tests/integration/test_v4_publication_recovery_floor.py; minimal production changes in src/salience/cycles/publication_recovery.py only if the tests expose a gap.

**Interfaces:** extend the existing test_concurrent_exact_hold_insert_returns_one_stable_hold, test_expired_permit_concurrent_exact_hold_insert_returns_one_stable_hold, test_hold_commit_then_process_death_replays_without_liability_change, test_cancel_hold_commit_then_process_death_replays_without_liability_change, and test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities. Reuse _liability_snapshot(database, operation_id), _assert_named_liabilities_unchanged(before, after), record_publication_recovery_hold(...), and verify_publication_recovery_rollback(database_url, *, workspace_id, operation_ids). Do not add a parallel race/crash harness.

- [ ] Extend both existing race tests to assert exactly one stable hold ID and exact v1 evidence for each authoritative reason. Do not create duplicate race fixtures.
- [ ] Extend both existing process-death tests to hard-exit after commit, replay the same hold ID, and compare the complete 21-key liabilities before/after. Keep old 16-key equality visible as a separate diagnostic.
- [ ] Add parameterized test_each_omitted_liability_collection_mutation_denies_recovery with exact IDs remote-publication-receipts, job-checkpoints, audit-events, and provenance-records. Cover applicable append/update/delete mutations. Each case proves the old 16-key projection unchanged; v1 replay and offline verifier deny; then the real rollback-restart launcher test in Task 4 denies before registration. Include no hold/liability mutation by the denial path.
- [ ] Add the omitted-collection and extended crash/race assertions before making any Task 6 implementation correction. Run the tests and confirm the new mutation checks expose any remaining gap; if a gap exists, implement only that narrow correction and rerun until green. The already-green Task 2 v1 behavior is the prerequisite, not a change to redo:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_holds.py tests/integration/test_v4_publication_recovery_floor.py -k 'concurrent_exact_hold_insert or process_death_replays_without_liability_change or each_omitted_liability_collection_mutation_denies_recovery or rollback_verifier_requires_both_reasons_and_unchanged_liabilities'
~~~
- [ ] Run the explicit rollback verification for expired_permit_receipt and published_during_cancel before any prior worker code is restarted. If 0043 populated downgrade is attempted, assert refusal and byte-identical rows.
- [ ] Commit the extended tests and any narrowly necessary fix as one reviewable commit.

### Task 7 — Prove zero adapter operations across containers with a neutral recorder

**Files:** tests/fixtures/v4_publication_floor_worker.py; new tests/fixtures/v4_publication_adapter_operation_recorder.py; new tests/fixtures/v4_publication_adapter_operation_receiver.py; tests/integration/test_v4_publication_recovery_floor.py; protocol reference src/salience/publication/contracts.py.

**Interfaces:** the recorder wraps PublisherAdapter and emits JSON to the independent receiver at POST /v1/adapter-events. Exact event shape: {"schema":"salience.adapter-operation.v1","run_id":"<uuid>","worker_id":"<uuid>","sequence":1,"operation":"<capabilities|preflight|prepare_delivery|create_or_resume|submit|reconcile|status|cancel|verify_webhook|refresh_capabilities>"}. The receiver returns 202 for a valid event and GET /v1/runs/{run_id}/events returns {"run_id":"<uuid>","events":[...]} sorted by sequence; the event contains no credentials or provider payload. The positive control reads events from the receiver container, not worker memory.

- [ ] Add test_cross_container_adapter_recorder_positive_control that exercises the capabilities property read and every listed method from the worker container, then confirms the independent recorder container receives exactly one event per exercise. Cover exactly preflight, prepare_delivery, create_or_resume, submit, reconcile, status, cancel, verify_webhook, and refresh_capabilities.
- [ ] Add parameterized test_cross_container_adapter_recorder_observes_zero_operations_for_both_holds with exact IDs expired_permit_receipt and published_during_cancel. Run both retained holds across worker and receiver containers; assert zero events from GET readback, both typed held outcomes, and unchanged full liabilities.
- [ ] Use neutral operation labels derived from the adapter protocol. Do not branch on provider, platform, external API, or upload type. Keep credentials absent and external effects disabled.
- [ ] Run the focused tests and confirm the zero assertion fails if a method is intentionally wired to the recorder, then passes when the hold path stops before adapter operations:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_floor.py -k 'cross_container_adapter_recorder_positive_control or cross_container_adapter_recorder_observes_zero_operations_for_both_holds'
~~~
- [ ] Re-run with both retained reasons and confirm the recorder's independent event store remains empty. Commit recorder/test fixture changes as one reviewable commit.

### Task 8 — Rebuild, rehearse, qualify, correct evidence, and complete final rereview

**Files:** exact candidate image and report artifacts; tests/fixtures/v4_publication_floor_worker.py; tests/integration/test_v4_publication_recovery_floor.py; deployment/v4/publication-worker-launch-lock.json; artifacts/v4-next-p1/agents/p1-publication-recovery-0043/publication-worker-launch-lock.sha256; artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/launch-v4-publication-worker.py; artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.json; artifacts/v4-next-p1/agents/p1-publication-recovery-0043/control-bundle/publication-worker-launch-lock.sha256; docs/v4/p0-release.json line 468 and docs/v4/documentation-status-audit.json line 1426 next_action fields, plus the other status files named in Task 0; additive M1 correction at artifacts/v4-next-p1/agents/p1-publication-recovery-0043/m1-report-digest-correction.json; ignored .superpowers/sdd/2026-10-13-p1-publication-recovery-floor-repair/implementation-report.md. Preserve historical evidence and the approved design content.

**Interfaces:** freeze source implementation at APP_COMMIT after Tasks 1–7. scripts/build_publication_worker_launch_lock.py consumes that commit, the retained wheel, and the extracted installed runtime tree; it writes the tracked lock, independent digest, and external control bundle. The image ID saved at `artifacts/v4-next-p1/agents/p1-publication-recovery-0043/worker-image-id.txt` is passed unchanged to image-bound tests and `start_rollback_worker(image, database_url, temporal_target, workspace_id, expired_operation_id, cancel_operation_id, task_queue, control_bundle, name)`. `stop_worker(name)` drains/stops the current fixture container; `start_rollback_worker(...)` uses Docker `--read-only` and starts only after launcher preflight and the read-only verifier succeed. Extend existing test_floor_worker_executes_both_retained_reasons_and_second_verifier_passes; add test_rollback_restart_launcher_verifies_both_holds_before_exec; and add test_candidate_read_only_worker_creates_no_pyc_and_preserves_runtime_root, which asserts Docker inspect `ReadonlyRootfs == true`, no `.pyc` under the authenticated runtime root, and a byte-identical runtime-root manifest before/after worker execution. The ignored implementation report records exact IDs, artifact hashes, command exits, review commits, and CI headSha without post-CI tracked edits.

- [ ] After Tasks 1–7 source work is complete, require a clean worktree, capture `APP_COMMIT` before any wheel or image build, and record that exact SHA in ignored implementation evidence. Build the retained wheel from that checked-out commit, then build and extract the exact Compose worker image using these existing Dockerfile/Compose inputs and explicit commands:
~~~
set -euo pipefail
APP_COMMIT="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"
ARTIFACT_DIR=artifacts/v4-next-p1/agents/p1-publication-recovery-0043
mkdir -p "$ARTIFACT_DIR" /tmp/p1-publication-recovery-0043/wheel-build
printf '%s\n' "$APP_COMMIT" > "$ARTIFACT_DIR/application-commit.txt"
.venv/bin/python -m pip wheel --no-deps --wheel-dir /tmp/p1-publication-recovery-0043/wheel-build .
install -D -m 0444 /tmp/p1-publication-recovery-0043/wheel-build/salience-0.1.0-py3-none-any.whl "$ARTIFACT_DIR/salience.whl"
test "$(git rev-parse HEAD)" = "$APP_COMMIT"
SALIENCE_WHEEL_FILE="$ARTIFACT_DIR/salience.whl" docker compose -p salience-p1-publication-recovery-0043 --profile application build worker
IMAGE_ID="$(docker compose -p salience-p1-publication-recovery-0043 --profile application images -q worker)"
test -n "$IMAGE_ID"
printf '%s\n' "$IMAGE_ID" > "$ARTIFACT_DIR/worker-image-id.txt"
CONTAINER_ID="$(docker create "$IMAGE_ID")"
mkdir -p /tmp/p1-publication-recovery-0043/site-packages
docker cp "$CONTAINER_ID:/usr/local/lib/python3.13/site-packages/." /tmp/p1-publication-recovery-0043/site-packages/
docker rm "$CONTAINER_ID"
test "$(git rev-parse HEAD)" = "$APP_COMMIT"
~~~
The wheel command builds from the clean checkout at `APP_COMMIT`; Compose receives that exact wheel through `SALIENCE_WHEEL_FILE`. Extract the installed `/usr/local/lib/python3.13/site-packages` tree and `RECORD` from the built worker image. Record wheel, image, interpreter, and disposable DB-image digests. Any code change after pinning requires regenerating all pins and repeating artifact-bound qualification.
- [ ] Regenerate the final candidate lock only after the exact wheel and installed runtime tree exist, using the same recorded `APP_COMMIT`:
~~~
set -euo pipefail
ARTIFACT_DIR=artifacts/v4-next-p1/agents/p1-publication-recovery-0043
APP_COMMIT="$(cat "$ARTIFACT_DIR/application-commit.txt")"
test "$APP_COMMIT" = "$(git rev-parse HEAD)"
.venv/bin/python scripts/build_publication_worker_launch_lock.py \
  --application-commit "$APP_COMMIT" \
  --wheel "$ARTIFACT_DIR/salience.whl" \
  --runtime-root /tmp/p1-publication-recovery-0043/site-packages \
  --lock deployment/v4/publication-worker-launch-lock.json \
  --independent-digest "$ARTIFACT_DIR/publication-worker-launch-lock.sha256" \
  --control-dir "$ARTIFACT_DIR/control-bundle"
cmp deployment/v4/publication-worker-launch-lock.json "$ARTIFACT_DIR/control-bundle/publication-worker-launch-lock.json"
cmp scripts/launch-v4-publication-worker.py "$ARTIFACT_DIR/control-bundle/launch-v4-publication-worker.py"
cmp "$ARTIFACT_DIR/publication-worker-launch-lock.sha256" "$ARTIFACT_DIR/control-bundle/publication-worker-launch-lock.sha256"
~~~
The generator must pin the exact wheel and installed RECORD, runtime imports, interpreter and launcher, and full source_files inventory: alembic.ini, migration package initializers, migrations/env.py, every revision from 0001 through 0043, and every loaded source/support file. The three Compose mount variables default to these matching files in `control-bundle/`; the launcher, lock, and digest are mounted read-only at their fixed `/run/salience-control/` paths. Verify exact inventory equality and the byte comparisons above before running authentic-inventory tests or image rehearsal.
- [ ] Search for dependent references before correcting M1: run `git grep -n -E '7f06f404226b346873755d76c6cffdcc7ddfb7ecad79d5de57f770b44010ebcf|65cb0ac34344f7d8931ceec32b214797c05e73a82dba4b64108184c9e9394836|m1-report-digest-correction.json' -- docs artifacts` and `grep -R -n -I -E '7f06f404226b346873755d76c6cffdcc7ddfb7ecad79d5de57f770b44010ebcf|65cb0ac34344f7d8931ceec32b214797c05e73a82dba4b64108184c9e9394836|m1-report-digest-correction.json' artifacts/v4-next-p1/agents .superpowers/sdd --exclude='plan-review.md' --exclude='planning-self-review.md' --exclude='2026-10-13-p1-publication-recovery-floor-repair.md'`. Update only dependent current report references to the corrected committed-bytes digest; if searches find none, explicitly record “no dependent references” in implementation-report.md. Add the report-only correction with both values and meanings at artifacts/v4-next-p1/agents/p1-publication-recovery-0043/m1-report-digest-correction.json. `artifacts/` is ignored, so retain this file as ignored report-only evidence and do not stage or claim it is committed. Tracked status/evidence summaries may record its path and corrected digest. Never rewrite historical evidence or counts.
- [ ] Correct stale handoffs/statuses to describe the 0043 repair and evidence sequence and preserve release holds. Complete and commit all intended tracked status/evidence/lock changes as one reviewable Task 8 evidence commit before final artifact qualification and final review. The ignored M1 addendum is not part of that commit; tracked summaries reference its ignored path and digest only. Keep status next_action focused on remaining production gates, so it remains accurate after CI.
- [ ] Run the installed-image Alembic command against a newly created disposable DB and verify exact head 0043. Seed both retained hold reasons using only test fixture data and budget 0. Keep both external effects disabled.
- [ ] Extend test_floor_worker_executes_both_retained_reasons_and_second_verifier_passes and add test_rollback_restart_launcher_verifies_both_holds_before_exec. Run verify_publication_recovery_rollback(database_url, workspace_id=workspace_id, operation_ids={"expired_permit_receipt": expired_operation_id, "published_during_cancel": cancel_operation_id}) before and after current-mode rehearsal. Require identical persisted hold IDs, exact 21-key snapshots, both reason bindings, unchanged liabilities, and both typed held outcomes; run it again after rollback-restart and record all three verifier exits.
- [ ] Before rollback-restart, wait until the current fixture worker's workflow histories and outbox are settled, drain and stop it, and confirm its container has exited. Extract workspace_id and both exact operation IDs from persisted hold rows; record them in implementation-report.md and verify expired_permit_receipt maps to expired_operation_id and published_during_cancel maps to cancel_operation_id.
- [ ] Start the actual read-only image via tests/fixtures/v4_publication_floor_worker.py::start_rollback_worker with the exact IDs recorded above; its Docker run must pass this in-container command unchanged:
~~~
/usr/local/bin/python -I -B -S /run/salience-control/launch-v4-publication-worker.py \
  --mode rollback-restart \
  --worker-kind legacy-fixture \
  --workspace-id "$WORKSPACE_ID" \
  --expired-hold-operation-id "$EXPIRED_PERMIT_RECEIPT_OPERATION_ID" \
  --cancel-hold-operation-id "$PUBLISHED_DURING_CANCEL_OPERATION_ID"
~~~
The external launcher compatibility preflight must run the read-only verifier for exactly both IDs before execve. Assert successful verification precedes worker registration; negative controls exit 78 before exec/registration and do not mutate holds or liabilities. A direct current-mode launch or in-process verifier alone does not satisfy this rollback-restart gate.
- [ ] Run the exact real-image rehearsal tests, including the existing both-reason fixture, verifier-before-exec assertion, and read-only/no-cache check:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_floor.py -k 'floor_worker_executes_both_retained_reasons_and_second_verifier_passes or rollback_restart_launcher_verifies_both_holds_before_exec or candidate_read_only_worker_creates_no_pyc_and_preserves_runtime_root'
~~~
- [ ] Add test_candidate_control_bundle_authenticates_full_0043_inventory with exact inventory case IDs exact-inventory, extra-file, missing-file, changed-file, symlink-path, and independent-digest-mismatch. Add parameterized test_candidate_image_launcher_denies_pin_mutations_before_registration with exact IDs artifact-digest-mismatch, lock-digest-mismatch, launcher-hash-mismatch, interpreter-hash-mismatch, distribution-record-mismatch, and runtime-root-mismatch. For each pin mutation, invoke the external launcher in a container from the exact built image with a per-case read-only control bundle; assert exit 78 before application import, worker registration, or exec.
- [ ] Before actual image rehearsal, run test_candidate_control_bundle_authenticates_full_0043_inventory against the generated lock, independent digest, wheel/RECORD, and control files. Require extra, missing, changed, symlinked, or digest-mismatched inventory to deny before metadata parsing, DB registration, or exec.
- [ ] Run these exact-candidate inventory, pin-mutation, and real rollback-launcher denial cases:
~~~
.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_floor.py -k 'candidate_control_bundle_authenticates_full_0043_inventory or candidate_image_launcher_denies_pin_mutations_before_registration or rollback_restart_launcher_denies_omitted_collection_mutation_before_registration or rollback_restart_launcher_denies_legacy_null_v1_before_registration'
~~~
- [ ] Run the final focused application suite and the retained Task 10 reason/migration/floor cases against the exact candidate:
~~~
set -euo pipefail
.venv/bin/python -m pytest -q tests/integration/test_v4_migrations.py tests/integration/test_v4_publication_recovery_holds.py tests/integration/test_v4_publication_recovery_floor.py
.venv/bin/python -m pytest -q \
  tests/integration/test_v4_publication_recovery_floor.py \
  tests/integration/test_v4_publication_recovery_holds.py::test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities \
  tests/integration/test_v4_publication_recovery_holds.py::test_migration_0042_schema_role_and_empty_rollback_reupgrade \
  tests/integration/test_v4_publication_recovery_holds.py::test_direct_sql_recovery_hold_guards_race_and_populated_downgrade
~~~
- [ ] Run all real-launch hostile-startup, malicious .pyc, 0042-empty, unauthenticated inventory, extra/missing/modified migration, omitted-collection, legacy NULL-v1, and valid positive controls against this exact built image/control bundle. Require the independent adapter recorder to show zero operations for both held reasons and pass its positive control.
- [ ] Run the complete non-live suite and required repository checks against this candidate:
~~~
set -euo pipefail
.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs
.venv/bin/python docs/v4/test_checks.py
.venv/bin/python docs/v4/check.py --self-test
.venv/bin/python docs/v4/check.py
for script in tests/scripts/*.sh; do bash "$script"; done
.venv/bin/python docs/v4/verify_diagrams.py --node-modules /tmp/salience-v4-mermaid/node_modules --browser /usr/bin/chromium --output artifacts/v4-next-p1/qualification-diagrams
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src tests scripts docs/v4
git diff --check
~~~
- [ ] Parse every changed JSON file with python -m json.tool. Record exact newly observed results only; do not transfer test counts from 7878208, 5cb5791, or any historical candidate. Record exact test outputs, artifact hashes, and qualification results in ignored implementation-report.md.
- [ ] Run corrected-publication rereview on the final tracked commit and exact artifact/control-bundle digests, including scope previously uncovered by 7878208/5cb5791, followed by the GPT-5.6 meta-review. Record the exact reviewed commit IDs and results before pushing. All tracked commits from Tasks 0–7 and the final Task 8 lock/status/evidence commit must already be in the reviewed commit; make no tracked evidence/status commit after the final review begins.
- [ ] Push only the feature branch after review; do not push or merge main. Inspect CI for the exact pushed headSha with:
~~~
set -euo pipefail
CI_HEAD="$(git rev-parse HEAD)"
gh run list --workflow v4-documentation.yml --branch "$(git branch --show-current)" --json databaseId,headSha,status,conclusion
CI_RUN_ID="$(gh run list --workflow v4-documentation.yml --branch "$(git branch --show-current)" --json databaseId,headSha,status,conclusion | jq -r --arg head "$CI_HEAD" '[.[] | select(.headSha == $head and .status == "completed" and .conclusion == "success")][-1].databaseId // empty')"
test -n "$CI_RUN_ID"
CI_RUN_JSON="$(gh run view "$CI_RUN_ID" --json headSha,status,conclusion,jobs)"
printf '%s\n' "$CI_RUN_JSON" | jq -e --arg head "$CI_HEAD" '.headSha == $head and .status == "completed" and .conclusion == "success" and ([.jobs[] | select(.name == "verify" or .name == "p0-regression") | .conclusion] | sort == ["success", "success"])' >/dev/null
test "$(git rev-parse HEAD)" = "$CI_HEAD"
~~~
Require the selected run headSha to equal git rev-parse HEAD and both verify and p0-regression jobs green. The required full suite and docs checks above must belong to this exact candidate, not a later or earlier head.
- [ ] If any tracked change is required after exact-head CI, create a new candidate commit, rerun artifact-bound checks if code/artifact changed, rerun affected tests/docs and both reviews, push the new head, and require exact-head CI green again. Make no tracked edit after the final green run.
- [ ] Capture final exact-head CI outcome in ignored implementation-report.md only. Preserve production effects disabled, fixture budget 0, RG0/full RG1 HELD, P2–P7 gated, P6 disabled, AgentTube untouched/reference-only, and main unmerged. All tracked status/evidence commits must already be included in the reviewed and pushed head.

## Final Self-Review

Before implementation starts, verify the plan covers every acceptance item in the repair spec: populated 0042 upgrade and 0043 constraints/grants/trigger/downgrade; independent omitted-collection mutations; legacy NULL-v1 denial and 0042-empty startup denial; both-reason concurrency/crash replay and unchanged full liabilities; hostile startup, .pyc rejection, and read-only no-cache behavior; exact authenticated migration inventory; installed-image Alembic migration against a disposable DB; read-only exact-artifact rehearsal with zero cross-container adapter operations; full non-live, docs, JSON, shell, diagram, pip, compile, and diff checks; M1/stale-handoff correction without historical rewrite; exact-candidate corrected-publication rereview, meta-review, and CI. Preserve existing completed hold behavior and tests; do not duplicate existing race/crash fixtures or rewrite 0042.
