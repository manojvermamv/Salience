# Task 13 report — final exact candidate and active evidence lock

> **Historical candidate:** this report records source `8d26aa15ef8d6bf7c9248bdd1899cf01a352cb2a` and artifact `e805c277577da8491dca51933dadb5c36acc805b`. Task18 later qualified source `c23c4cca72021ce6621eaacd66f2bfc23f4cc129` and artifact `be6f713b255f6b654b73ae4343db1db5c07e3840`; current evidence is in [`task-15-report.md`](task-15-report.md) and the [final evidence report](final-evidence-report.md).

Date: 2026-10-29
Status: local candidate build and tests complete; documentation/evidence verification recorded below. External qualification and final independent review remain outstanding.

## Exact source and generated artifact

- Implementation source: commit `8d26aa15ef8d6bf7c9248bdd1899cf01a352cb2a`, tree `435f7b6cea818c96790e890da9485ce08fd18ac6`.
- Artifact commit measured by the focused groups and one final full suite: `e805c277577da8491dca51933dadb5c36acc805b`, tree `2e7213cf989f8b7dcfd489afb076be54c4858a8b`, parent exactly the source commit above. It changes only `deployment/v4/publication-worker-launch-lock-v2-development.json` and its `.sha256` companion. Source files were not changed in this evidence task.
- Two separate clean source exports and isolated PEP 517 wheel builds used the source commit timestamp as `SOURCE_DATE_EPOCH`. The wheel builds were byte-identical (`cmp` exit 0): SHA-256 `267c793ea403f79fe35bcb5f6a33edca32d5affa0dcf41d14eb633ef47cddc6a`, 455,511 bytes.
- The local development OCI image digest is `24ff7e27e99cb978307bf0b16390749536a29ad30fe36bc47150de67ac61e13d`. The source-manifest digest is `b1834357f140bef0a855c096eb4b8d5e4b2acc38862d911af4e440d1da743c15`.
- Two invocations of the established Task 11 v2 lock builder emitted identical JSON and companion content (`cmp` exit 0). The v2 JSON SHA-256 and companion content are `c788d439807855822b3e23091545f31109c333fab79fcca319c4ca9c71c26ab1`; the companion file SHA-256 is `800ca76339bc5a79729d10d9cf77a0bcbc577a555266ad1d5e003910cd725a61`. The v1 launch lock remains byte-identical at SHA-256 `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.
- The development route is UUID `00000000-0000-0000-0000-000000000953`, tuple SHA-256 `3ab3efdaa3c7b79ac74352da2f2d4a4990014cf07c8195c1bf08e7a6256eecbe`, and authenticated route-manifest SHA-256 `052caff03ba3d5d0f38dcd90a054c83ebdf25e34bf7e42a3cc14b446523b97cb`. Migration head is `0051_p1_recovery_rollback_v2`; rollback verifier ABI is `salience.p1-recovery-rollback-verifier.v2`. The external production trust digest remains unprovisioned.

An initial temporary build stopped before image generation because its uppercase temp-directory suffix produced a Docker-invalid tag. The disposable tag was normalized to lowercase and the deterministic two-build process completed; no source or tracked artifact was changed by the failed attempt.

## Focused and full test evidence

All groups ran serially against the exact artifact commit in one unique Compose project, `salience-p1-task13-focusfull-20261029-2999355`. Only local Postgres and Temporal ran. Postgres was migrated and checked at `0051_p1_recovery_rollback_v2`; the test process used `env -i`, repository `PYTHONPATH`, and local-only database/Temporal endpoints. Provider, production, alert, live, and external credentials were unset. Every pytest command used `-m 'not live'`.

The shared pytest prefix was:

```bash
.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short
```

Each focused command exited 0:

1. Route and launcher contracts, plus the exact-head lock builder:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi
   ```

   34 passed in 2.00 seconds (runner elapsed 2 seconds).

2. Migrations 0047–0051 and corrected rollback inventory:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k '0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety'
   ```

   39 passed, 88 deselected in 253.34 seconds (runner elapsed 254 seconds).

3. Schedule and native schedule regressions:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py
   ```

   51 passed in 212.81 seconds (runner elapsed 213 seconds).

4. Complete recovery and alerts:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_recovery.py
   ```

   54 passed in 382.68 seconds (runner elapsed 384 seconds).

5. Rollback-v2 readback and handoff:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists
   ```

   4 passed in 0.93 seconds (runner elapsed 1 second).

6. Policy and revocation:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py
   ```

   133 passed in 71.20 seconds (runner elapsed 72 seconds).

7. External-acceptance checker unit suite:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_p1_external_acceptance.py
   ```

   45 passed in 189.35 seconds (runner elapsed 190 seconds).

After all focused groups passed, the full non-live suite ran exactly once on that same artifact commit:

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs --tb=short
```

Exit 0; 1,150 passed, 26 skipped, 3 warnings in 2,414.18 seconds (runner elapsed 2,417 seconds). The warnings are Alembic `path_separator` deprecations in three historical owner-binding migration cases. The 26 skips are the documented absent Task 8 exact-candidate state/fixture cases. No full-suite rerun was performed. Per-command logs and the captured full output remain under `/tmp/salience-p1-task13-build-20261029-ytyl4i/verification/`.

## Synthetic acceptance package and held gates

`docs/v4/p1-remaining-production-development-evidence.json` is a synthetic local package bound to the source, artifact, wheel, image, source manifest, lock, route, and migration head above. Package digest: `ef310caaa0653e14629d3e87cc72f1e29cdffa7edb52e03263a02a819cf2181d`; manifest SHA-256: `d9a2f2054b408f8d206bca05b2b226e9c31cd4374e43013704c4ccfc6cb29b84`; package-file SHA-256: `391d30b627470082d27bdb0a2b320ddee672de7bc49f52700dc7b041dd828f78`.

The package has one synthetic route and 22 rows: five RG0 and seventeen RG1. Every row is `NOT RUN`; every owner/operator/reviewer/final-auditor identity is synthetic and unverified. Production effects are disabled, the publication budget is zero, final audit remains `NOT RUN`, disposition is `NO_GO`, and no gate mutation is claimed. The required acceptance command returned exit 1 with `structural_status: VALID`, `disposition: NO_GO`, and `gate_state: UNCHANGED`:

```bash
.venv/bin/python scripts/check-p1-external-acceptance.py --package docs/v4/p1-remaining-production-development-evidence.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
```

All external qualification remains absent. RG0 and full RG1 remain `HELD`; P2 remains `BLOCKED`; production effects remain disabled.

## Active documentation lock and repository checks

`docs/v4/evidence-lock.json` is the active current-source lock required by CI, not frozen historical evidence. Its historical nested lock records were preserved. The active application commit/scope now names source `8d26aa15ef8d6bf7c9248bdd1899cf01a352cb2a` / tree `435f7b6cea818c96790e890da9485ce08fd18ac6`; the exact inventory is 409 current files under `src`, `tests`, `migrations`, and `scripts`, plus the seven existing build/dependency boundary files, 416 total. Lock SHA-256: `02e74f951fe8ab9ff6fd893c2bb93221ea59fbd30c81e0cb4090d9fa6f1d2c82`.

Both `.venv/bin/python docs/v4/check.py --self-test` and `.venv/bin/python docs/v4/check.py` exited 0 after the lock refresh (75 requirements, 416 evidence files, 360 inventory files, 111 Markdown files, 4 HTML files; six self-test negative controls). The final CI-equivalent documentation and static checks also passed: `python3 docs/v4/test_agenttube_reference.py` exited 0 (17 tests, 2.962 seconds), `python3 docs/v4/verify_agenttube_reference.py` exited 0 (`PASS`), and `.venv/bin/python docs/v4/test_checks.py` exited 0 (25 tests, 0.220 seconds). All five required shell contracts exited 0; `.venv/bin/python -m compileall -q src tests` exited 0; and Archify validation exited 0 with all nine checks passing and zero composition errors or warnings. The acceptance checker exited 1 as required with `structural_status: VALID`, `disposition: NO_GO`, and `gate_state: UNCHANGED` for the same synthetic package. `git diff --check` exited 0 on the final evidence diff. The refreshed status wrapper covers 304 documentation artifacts, 129 Markdown files, and 36 current handoff hashes; its own recursive digest remains null.

## Cleanup and limitations

The test-run cleanup trap exited 0 and removed only the exact Compose project. Post-cleanup queries found zero project-labeled containers, volumes, or networks. The temporary image-build resources were removed by exact name; no host-wide prune ran. No live provider, production database, remote schedule, alert target, or external service was contacted. No push or merge occurred. Final independent read-only reviews and exact-head CI remain controller-owned; this report is local development evidence, not production qualification.

## Task 13 metadata correction — 2026-10-29

The main meta-review confirmed the finding: `verification.current_full_non_live_application_commit` named stale source `ec12763`, while the adjacent full-suite result is the Task 13 run with 1,150 passed. In these records, `application_commit` identifies the implementation source, so the active alias now names source `8d26aa15ef8d6bf7c9248bdd1899cf01a352cb2a`; tested artifact `e805c277577da8491dca51933dadb5c36acc805b` remains explicit in the status and candidate fields.

The stale root-level current test/source/status aliases were aligned with Task 13: seven focused groups (360 passed total), the one full non-live run (1,150 passed / 26 skipped / 3 warnings), source `8d26aa1`, and artifact `e805c27`. The Task 6 fixture checkpoint objects remain unchanged as preserved historical evidence tied to their `ec12763` verifier log. No source, live/provider, or external state changed.

The correction is documentation-only. The focused documentation integrity test, complete `docs/v4/test_checks.py`, both `docs/v4/check.py` modes, expected synthetic acceptance result, and whitespace check are recorded after final metadata refresh below.

### Documentation correction validation

- Focused inventory/handoff integrity test: `.venv/bin/python docs/v4/test_checks.py GateChecks.test_documentation_status_audit_covers_current_artifacts_and_hashes` — exit 0, 1/1 test passed.
- Complete documentation checks: `.venv/bin/python docs/v4/test_checks.py` — exit 0, 25/25 tests passed.
- Normal checker: `.venv/bin/python docs/v4/check.py` — exit 0; 75 requirements, 416 evidence files, 360 inventory files, 111 Markdown files, 4 HTML files, zero errors.
- Checker self-test: `.venv/bin/python docs/v4/check.py --self-test` — exit 0; same counts, six negative controls, zero errors.
- Synthetic acceptance checker: `.venv/bin/python scripts/check-p1-external-acceptance.py --package docs/v4/p1-remaining-production-development-evidence.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json` — expected exit 1; `structural_status=VALID`, `disposition=NO_GO`, `gate_state=UNCHANGED`.
- `git diff --check` — exit 0. The refreshed wrapper has 304 artifact rows, 303 verified non-self hashes, one intentional null self-hash, and 36 verified current handoff hashes.

## Task 14 source-review corrections — 2026-10-29

The final review correction commit is `becb3951d96f395b2c3ec9303e578dfb1e7eda5d` / tree `3d9bd870d2a46b805106602cfcacb92bbba7b55a`. It corrects routed lease-renewal identity, alert restart after a committed-attempt crash, exact 0051 permit-issue payload validation, and publication-hold source/reason revalidation. Focused regressions and red/green results are recorded in [`task-14-report.md`](task-14-report.md); route (4), alert crash/retry (12), normal delivery/escalation (3), issue/claim event (13), and hold/liability (8) selected cases pass. Touched files compile and whitespace validation passes. No full suite or exact-candidate artifact/evidence rebuild was performed.

The Task 13 wheel, candidate lock, synthetic package, and active evidence lock remain historical and still identify source `8d26aa15`. They are not evidence for the Task 14 source. The controller owns creating and validating the new exact candidate after review. Production effects remain disabled; RG0/full RG1 remain `HELD`; P2 remains `BLOCKED`; external qualification remains `NOT RUN`; no push, merge, or live-system contact occurred.

## Historical-scope follow-up — Task18 supersession

Task13’s build, 360 focused tests, 1,150-pass full suite, artifact hashes, and then-current lock remain valid only for the Task13 source/artifact stated above. They are superseded as the current local candidate record by Task18’s exact qualification: source `c23c4cca72021ce6621eaacd66f2bfc23f4cc129` / tree `d587cda6011bb0b974346353cec7a4856fbaf778`, artifact `be6f713b255f6b654b73ae4343db1db5c07e3840` / tree `419a33459a022d96c843313ffe5112c4e863c862`, 399 focused passes, and 1,189 passed / 26 skipped / 3 warnings in the one full non-live run. The active source lock and synthetic package now bind the newer exact candidate. Task13’s prior record is retained with its original scope.
