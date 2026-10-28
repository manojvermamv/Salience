# SDD ledger — plan: docs/superpowers/plans/2026-10-28-p1-remaining-production-development.md

Planning and implementation ledger for P1 remaining production development.

Plan: `docs/superpowers/plans/2026-10-28-p1-remaining-production-development.md`
Planning baseline: branch `arch`, commit `0763702bbedda7ffc99c3b526a5d018340e63aae`; initial working tree clean.

## Scope and rulings

- This is a documentation/specification and planning pass. It does not implement application code, run the planned migration or acceptance suites, contact external systems, enable production, push, or merge.
- The frozen initial audit remains bound to source `7c1ddcda280b510048ad61106c406171edd64f18` and its original environment manifest. Its audit JSON/report, Slice 2 evidence, qualification matrix, and historical 0043 evidence remain unchanged. The audit-review wrapper receives a separately labeled current-text digest addendum; the live documentation-status inventory receives current hashes/counts only, without changing historical review verdicts.
- Current code uses `RouteBinding.route_id: UUID`, and migration 0044 stores `route_id uuid`. Ruling: preserve `route_id` as a stable UUID; define `route_tuple_sha256` as separate canonical route-tuple evidence in the authenticated manifest and permit snapshot. This corrects the old deterministic-SHA `route_id` prose without altering 0044–0046 history.
- Current Alembic head is `0046_production_policy_approval_id_immutability`. `0043_complete_publication_recovery_liabilities` remains the historical fixture floor. The plan adds one lineage from 0046: 0047 recovery work/events, 0048 production schedule bindings, 0049 typed production permit reissue, and 0050 complete rollback verifier v2.
- Slice 2 retains the local implementation and evidence at application candidate `ec12763e38143f52f1af2661e00932158982f11a`; the progress ledger records 908 passed, 26 skipped, 3 warnings on its non-live suite and the 0046 head. This remains fixture/development evidence only. It does not qualify production or satisfy the absent typed same-operation no-effect reissue path.
- Routing is absent. Ruling: extend the existing authenticated launcher/compatibility path and reuse `ProductionEffectDispatch`; provide a provider-neutral lease/control contract and deterministic fake. `run_worker()` remains fixture-only until target configuration and qualification exist.
- The production schedule adapter and production source binding are absent. Ruling: add a separate injected `ProductionTemporalScheduleControl` plus additive source binding; keep `TemporalFixtureLegacyScheduleControl` and fixture target/queue checks intact.
- Production recovery work/events, remote-read adapter, external alert delivery, typed same-operation no-effect generation reissue, and complete rollback verifier v2 are absent. Ruling: use the production recovery event ledger for one-use typed no-effect command evidence, keep the existing case state machine, and let an additive 0049 migration consume the event for same-operation permit generation advance. Existing 0044 fixture/test assumptions remain historical; no prior rows are recast.
- The 2026-10-27 Slice 2 progress note proposed a `v4_cycle_events` proof for fixture test setup while explicitly leaving runtime generation advance unavailable. That proposal is historical test setup, not an implemented production command. The new 0047 recovery event contract supplies the future typed command receipt; 0049 consumes it once without rewriting or relabeling earlier Slice 2 history.
- The external target, named owners/operators/reviewers, credentials, provider/account, live schedule inventory, alert channel, versioned operations limits, and route lease platform are absent. Ruling: local provider-neutral contracts, injected fakes, and package/checker tests are in-scope; target procedures stay `NOT RUN`/`HELD`.
- Global status stays production disabled, fixture publication budget zero, RG0/full RG1 `HELD`, P2 `BLOCKED`, and `main` unmerged.

## Plan decomposition

1. Canonical UUID route manifest and tuple digest.
2. Migration 0047 recovery work/events.
3. Migration 0048 production schedule source bindings.
4. Route lease/action contract and deterministic fake.
5. Authenticated launcher and route-bound worker lifecycle.
6. Production schedule adapter over the existing cutover state machine.
7. Recovery reads, typed commands, deadlines, and alerts.
8. Migration 0049 plus same-operation no-effect permit reissue.
9. Migration 0050 complete rollback verifier v2 and launcher ABI v2.
10. Local 22-row external acceptance package checker.
11. Exact-candidate development evidence and final non-live verification.

The plan traces all 17 Slice 2 policy/revocation acceptance cases, all 7 routing tests, all 12 schedule tests, all 13 recovery tests, and all 20 external checker tests to tasks. Named-target acceptance is explicitly outside the plan and remains `NOT RUN`/`HELD`.

## Cost and execution status

- Planning cost: one planning agent, zero delegated agents, zero application-code changes, zero live/external requests, and zero heavy tests.
- Planned implementation cost: 11 independently reviewable task commits and four additive migrations. Each task has red-first tests and explicit downgrade/rollback behavior where it owns schema or prior-code safety.
- Completed in this pass: read the four updated specs, initial gap audit, Slice 2 policy design/plan/evidence/progress, current routing/worker/schedule/recovery code, and migrations 0044–0046; currentize the four specs; draft the master implementation plan.
- Self-review rulings: route tuple array/set ordering is canonicalized before hashing; schedule target methods distinguish scoped inventory from single-schedule pause/resume; the recovery alert contract explicitly implements the schedule alert sink; rollback verification takes both route lease and complete Temporal worker readback.
- Validation: the first `docs/v4/test_checks.py` run exposed two current-index gaps (new Markdown paths and current spec hashes). The append-only wrapper digest addendum and live inventory refresh resolved them without changing the frozen initial audit or prior review verdicts. The final docs-only run passed: `docs/v4/test_checks.py` 25 tests; `docs/v4/check.py --self-test` and normal mode, each 75 requirements / 376 evidence files / 360 inventory files / 110 Markdown files / 4 HTML files, with 6 negative controls in self-test; all five `tests/scripts/*.sh` contracts; changed JSON parsing; and staged/unstaged `git diff --check`.
- Cost through validation: one planning agent; zero delegated agents, application-code changes, application/migration tests, heavy tests, live/external requests, or provider/production access. Docs-only checks comprised 25 unit checks, 6 validator negative controls, 5 shell contracts, JSON parsing, and whitespace checks.
- Current status: planning artifacts are complete in one local documentation commit; its SHA is reported in the handoff. No push or merge.

## Execution resume — 2026-10-28

- Resume HEAD: `dccb6f34dc121e2407c26d39ae3eeb7c3ea8b32d`; implementation baseline before Task 1 will be recorded separately.
- Workspace ruling: `/home/admin/Salience` is the single normal checkout on the dedicated `arch` feature branch. The user previously required abandoned worktrees to be reconciled and development to continue on this branch; creating another linked worktree would contradict that direction. `main` remains unmerged.
- Baseline ruling: the application baseline at `0763702bbedda7ffc99c3b526a5d018340e63aae` passed exact-head CI and the full non-live suite in the preceding goal. Commits through `dccb6f3` are documentation-only and passed the repository documentation checks. Do not repeat the heavy suite before Task 1; every implementation task runs its focused red/green checks and Task 11 runs the full exact-candidate suite.
- Plan review: READY with no Critical findings. Two Important Task 9 clarifications and one Minor file-ownership clarification were corrected in `dccb6f3`: launcher payload pins exact head 0050 and verifier ABI v2; 0050 downgrade raises before schema or Alembic-version mutation; exact Task 9 test files are named.

### Preflight shared-file and interface scan

| Tasks | Producer / consumer relationship | Finding / ruling |
|---|---|---|
| 1 → 4 | Task 1 creates canonical route manifest and tuple evidence; Task 4 extends the same module with lease/action authority. | Clean; Task 4 must reuse the canonical hash and UUID route ID, not create another route identity. |
| 1 → 5 | Task 5 authenticated worker startup consumes Task 1 manifest. | Clean; production startup stays fail-closed when manifest or authority is absent. |
| 1 → 8 | Task 8 permit snapshot v2 consumes `route_tuple_sha256`. | Clean; historical snapshot v1 stays readable and new rows require v2 after 0049. |
| 2 → 3 | 0047 is the parent of 0048. | Clean; one linear additive migration chain. |
| 2 → 7 | Task 2 produces recovery work/events tables; Task 7 implements their repository and coordinator. | Clean; Task 7 reuses existing case transitions and does not add a second state machine. |
| 2 → 8 | Task 8 consumes a one-use typed event from Task 2/7 through an FK. | Clean; command evidence is append-only and cannot authorize multiple permits. |
| 2 → 9 | Task 9 inventories recovery work/events. | Clean; inventory is read-only and self-discovered. |
| 3 → 6 | Task 3 creates immutable schedule source bindings; Task 6 implements the controller. | Clean; fixture schedule guards remain unchanged. |
| 3 → 8 | 0048 is the parent of 0049. | Clean; no rewrite of earlier migrations. |
| 3 → 9 | Task 9 inventories production schedule bindings. | Clean; missing or incomplete bindings deny rollback. |
| 4 → 5 | Task 5 consumes route lease, action, slot, and readback contracts from Task 4. | Clean; Task 5 owns lifecycle wiring, Task 4 owns authority semantics. |
| 4 → 6 | Task 6 consumes route authority for each schedule action. | Clean; the schedule adapter has no provider-effect authority. |
| 4 → 9 | Task 9 consumes complete route readback. | Clean; unavailable/unknown authority denies prior-code restart. |
| 5 → 9 | Task 9 evolves the same launcher/lock and consumes worker readback. | Resolved in `dccb6f3`: Task 9 must regenerate/finalize the authenticated payload for exact migration head 0050 and verifier ABI v2. |
| 6 → 7 | Task 6 defines `ScheduleAlertSink`; Task 7's alert adapter implements it. | Clean; acknowledgment remains receipt-only and cannot clear holds. |
| 6 → 9 | Task 9 inventories schedule bindings and Temporal registration/drain readback. | Clean; later-slot readback is mandatory before prior-route restart. |
| 7 → 8 | Task 7 writes the typed no-effect command; Task 8 consumes it for same-operation generation advance. | Clean; operation, request fingerprint, and provider idempotency key remain unchanged. |
| 7 → 9 | Task 9 inventories recovery events, liabilities, and unresolved work. | Clean; no recovery action mutates fixture holds. |
| 8 → 9 | 0049 is the parent of 0050; Task 9 inventories permits, claims, and current pointers. | Clean; 0050 downgrade is an explicit fail-before-mutation operation, avoiding a false Alembic head. |
| 10 → 11 | Task 10 creates the local package checker; Task 11 runs it against development evidence. | Clean; structural validation cannot create external PASS or release gates. |
| 1–10 → 11 | Task 11 verifies and records the exact combined candidate. | Clean; any FAIL, NOT RUN, HELD, stale evidence, or candidate/environment mismatch keeps P2 blocked. |

### Preflight task self-consistency scan

| Task | Tests against implementation/files | Finding / ruling |
|---|---|---|
| 1 | Unit route hashing plus existing policy fixture builder; new module and dispatch snapshot fields. | Clean; optional tuple field reads legacy bindings while new manifests require it. |
| 2 | Migration tests cover schema, append-only behavior, exact replay, and safe downgrade. | Clean; migration follows actual head 0046. |
| 3 | Migration tests cover target/namespace identity, immutable binding, and safe downgrade. | Clean; migration follows 0047. |
| 4 | Integration tests cover acquire/renew/CAS/drain/readback through deterministic fake. | Clean; provider-neutral protocol only. |
| 5 | Launcher and worker tests cover authenticated v2 route keys while preserving fixture v1. | Clean; default/unconfigured production remains disabled. |
| 6 | Twelve schedule acceptance tests exercise injected fake and existing cutover state machine. | Clean; no create/update/cadence-selection authority exists. |
| 7 | Recovery tests cover source adoption, scoped reads, alerts, deadlines, concurrency, and no-effect command. | Clean; pre-admission work never constructs a remote client. |
| 8 | Migration and dispatch tests cover snapshot v2 and one-use same-operation reissue. | Clean; generation 1 historical path remains readable. |
| 9 | Exact named migration, launcher, routing/readback, and recovery test files cover inventory and ABI v2. | Clean after `dccb6f3`; downgrade must raise with schema and `alembic_version` unchanged at 0050. |
| 10 | Twenty unit tests cover exactly 22 rows, digests, scope, freshness, rollback, and non-mutation. | Clean; CLI has no external client or gate mutation method. |
| 11 | Focused, full non-live, docs, checker, evidence, and exact-candidate checks. | Clean; live/external procedures remain NOT RUN/HELD and cannot be inferred from local results. |

- Preflight result: no unresolved contradiction blocks Task 1. Every implementation task uses red-first tests, commits once focused green, and receives an independent task review before the next task.

## Task execution

- Task 1 BASE: `08f0230098873774cc76a869fc8362edccb9471e`; implementer `/root/p1_task1_route_manifest` (GPT-6 Luna Max); report `task-1-report.md`.
- Task 1 ruling: the authenticated manifest wire payload has exact schema `salience.p1-production-route-manifest.v1` and keys `schema_version`, `route_id`, `route_tuple`, `route_tuple_sha256`, `worker_build_id`, `slots`, `artifact_sha256`, and `image_sha256`; bundle schema is `salience.p1-production-route-bundle.v1` with exact keys `schema_version` and `routes`. `worker_build_id` is required by the routing spec at manifest level and remains outside the tuple digest. The verified outer artifact-lock digest is runtime context, excluded from the embedded payload and manifest digest. Queue selection exists only at authenticated `route_tuple.task_queue`; exact-key parsing rejects overrides and Task 5 must expose no separate queue selection. Cost if wrong: Task 5 launcher compatibility and production route evidence would require a versioned manifest-contract correction.
- Task 1 TDD: RED was the expected missing `salience.cycles.production_routing` import. GREEN passed 4 route tests, 5 existing production-policy unit tests, and 105 policy/revocation integration tests on a fresh current-source disposable database; combined result 114 passed. The pre-existing reusable local database contained a stale stored 0044 dispatch function despite an 0046 marker, so it was not accepted as valid regression evidence.
- Task 1: complete (commits `08f0230..b095c91`, review clean). Reviewer `/root/p1_task1_route_review` returned spec PASS, quality PASS, no Critical/Important/Minor findings; no tests were repeated by the reviewer.

## Task 9 plan review clarification

- Add an explicit finalization step for the authenticated launcher v2 candidate payload: the builder regenerates it with exact `production_migration_head = "0050_p1_recovery_rollback_v2"` and rollback verifier ABI v2. `test_launcher_v2_lock_builder_binds_exact_final_migration_head_0050_and_verifier_abi` in `tests/integration/test_v4_publication_recovery_floor.py` pins this. The generated candidate remains development-only; the external production trust digest, endpoint, and credentials remain absent, and the tracked fixture v1 lock is preserved.
- Migration 0050 downgrade raises before any schema mutation or `alembic_version` change. `test_0050_downgrade_raises_without_schema_or_alembic_version_change` in `tests/integration/test_v4_migrations.py` must assert the complete schema inventory/fingerprint and version row remain unchanged at 0050 after the attempted downgrade.
- Task 9 now assigns named cases to exact files: launcher lock/preflight in `tests/integration/test_v4_publication_recovery_floor.py` and `tests/integration/test_p1_production_routing.py`; inventory/recovery in `tests/integration/test_p1_production_recovery.py`; migration/downgrade in `tests/integration/test_v4_migrations.py`.
- Follow-up validation: `python3 docs/v4/test_checks.py` passed 25 tests; `python3 docs/v4/check.py --self-test` and normal mode passed (75 requirements / 376 evidence files / 360 inventory files / 110 Markdown files / 4 HTML files, with six negative controls in self-test); all five `tests/scripts/*.sh` contracts passed; JSON parsing and staged/unstaged `git diff --check` passed.
- Follow-up scope/cost: documentation and planning metadata only; zero application-code edits, migration/acceptance tests, external requests, or provider/production access. No push or merge.
- Follow-up status: Task 9 clarifications and current inventory digests are complete in a local documentation-only change. The tracked v1 fixture lock, production-disabled state, RG0/full RG1 `HELD`, P2 `BLOCKED`, and `main` unmerged state remain unchanged.

## Recovery-origin correction — 2026-10-28

- Verified schema ruling: shared Slice 2 intents/admissions/cycles/cases have no durable production discriminator, so migration 0047 adds `v4_production_intent_enrollments` as the typed immutable production-origin authorization relation, unique on `(intent_id, eligibility_revision)`. It binds workspace/goal/revisions, source owner, subject/revision/grant IDs, policy version/hash, workspace+goal stop revisions, artifact SHA, route/slot/route owner/epoch, manifest/external lock digests, and exactly one route-fence decision ID. There is no backfill or adoption of unmarked rows.
- Ownership ruling: Task 6's provider-neutral schedule/admission path is the sole writer. It reloads canonical facts and atomically enrolls only after current policy, both stop revisions, and route fencing pass. General enrollment writes are revoked; Task 7 may consume a matching immutable row only, never create one or infer production origin. Task 2 defines schema and SQL guards; Task 6 adds writer and fake-backed atomicity/staleness tests; Task 7 tests exact source consumption; Task 9 includes enrollments in the complete rollback inventory.
- Recovery source ruling: `cycle_case.source_id = case_id`, `pre_admission_intent_case.source_id = intent_id`, and self-contained `production_publication_hold.source_id = operation_id` with `work_id` as stable hold ID. Cycle-case reason copies the exact allowlisted `failure_class` (`policy_denial`, `technical_failure`, `quality_failure`, `unknown_effect`, `admission_review`, `deadline`, `manual_review`) and its state copies one of `retry_due`, `reconciling`, `rework_due`, `awaiting_review`, `suspended`. Hold reasons are `expired_permit_receipt`/`published_during_cancel`, state `held`; pre-admission reasons are `admission_deferred`/`admission_review_required` mapped to exact admission disposition, state `unadmitted`. No second case state machine is introduced.
- Production hold snapshot ruling: use exactly `salience.p1-production-publication-liability.v1` with 22 keys: `production_intent_enrollment`, `goal`, `intent`, `cycle`, `context`, `job`, `production_current_dispatch`, `production_permits`, `production_permit_claims`, `publication_request`, `publication_plan`, `publication_attempt`, `external_effect`, `budget_reservation`, `output`, `remote_publication_receipts`, `publication_status_events`, `cycle_events`, `cycle_outbox`, `job_checkpoints`, `audit_events`, `provenance_records`. Keep `salience.publication-liability.v1` as the historical 21-key fixture contract, never a production snapshot. Cases/intents use database-derived fingerprints and NULL liability snapshots.
- Plan changes: Task 2 now names all three 0047 relations, exact source and snapshot rules, migration RED/GREEN tests in `tests/integration/test_v4_migrations.py`, no-backfill assertions, and downgrade safety. Task 6 names the enrollment writer and fake tests in `tests/integration/test_p1_production_schedule_fencing.py` plus recovery handoff tests. Task 7 consumes enrollment; Task 9 inventories and validates it. Historical plan-review notes above remain historical; the current plan and recovery-spec hashes are refreshed in the audit inventory after validation.
- Cost/scope: one planning agent; zero application or migration file edits, live/external requests, provider/production access, or heavy tests. Global status remains effects disabled, fixture publication budget zero, RG0/full RG1 `HELD`, P2 `BLOCKED`, and `main` unmerged.
- Validation: `python3 docs/v4/test_checks.py` passed 25 tests; all five `tests/scripts/*.sh` contracts passed; the status-audit JSON parsed; and `git diff --check` passed. Both `python3 docs/v4/check.py --self-test` and normal mode exited 1 with the same expected in-progress evidence-staleness findings: changed `src/salience/cycles/production_dispatch.py` and `tests/integration/test_p1_production_policy_revocation.py`, plus new `src/salience/cycles/production_routing.py` and `tests/unit/test_production_routing.py` outside the frozen active Slice 2 evidence set. This Task 1 implementation is not blessed into that historical lock; exact-candidate evidence refresh belongs to Task 11. No application/migration tests, external requests, provider/production access, or heavy tests were run.
- Cost/scope: one planning agent, documentation/spec/plan/current-inventory edits only; no application source or migration files changed, and no push. Commit SHA is reported in the handoff.
