# Task 15 brief — final exact candidate and evidence refresh

Date: 2026-10-29

## Frozen source

Implementation source is commit `237288e21f5489cbd7efa984a0fe4bc53aede392`, tree `dbc9a9919a9b8b281b6ee44e82b4c1980daf8883`. The branch begins this task clean in the sole primary worktree `/home/admin/Salience`. Do not edit or amend source. The candidate artifact must be a separate commit directly after this source.

## Required outcome

Build two deterministic PEP 517 wheels from independent clean exports, reproduce the Task 11/13 authenticated development v2 launch-lock process, and preserve the v1 lock. Build the local development image using only the established local development procedure; publish nothing. Commit only the generated v2 development lock and companion as the artifact commit.

Run the seven approved focused non-live groups serially on the artifact commit: complete production routing files; migrations/current exact head; schedule/native; complete production recovery; rollback-v2 handoff; complete policy/revocation; and external acceptance checker unit suite. Include all Task 14 route, alert, and 0051 issue/claim/hold regressions. Only after all focused groups pass, run the full non-live pytest suite exactly once:

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs --tb=short
```

If any focused group or the single full suite fails, stop and report the exact failure. Do not edit source, artifact, or rerun the full suite as a response to a failure.

Use one uniquely named disposable Compose project at a time, only local Postgres and Temporal, explicit local endpoints, credentials unset with `env -i`, and serial tests. The cleanup trap must remove only the exact project; verify zero project-labeled containers, volumes, and networks. Never use host-wide prune. Do not contact providers, production systems, live schedules, alerts, or external acceptance services. Do not push or merge.

After passing, refresh `docs/v4/evidence-lock.json` to the exact current source while preserving historical nested lock fields, and refresh the synthetic acceptance package, Task 13/14/final reports, current progress/audit/spec review/status wrapper. Keep all 22 external rows `NOT RUN`, identities synthetic/unverified, effects disabled, budget zero, final audit `NOT RUN`, disposition `NO_GO`, gates unchanged, and P2 `BLOCKED`. Avoid self-hashes.

Run CI-equivalent AgentTube tests and verifier, docs `test_checks.py`, normal and self-test docs checker, five shell contracts, compileall, Archify validation, acceptance checker (expected exit 1: `VALID` / `NO_GO` / `UNCHANGED`), and `git diff --check`. Refresh status-wrapper handoff hashes after final text and rerun affected checks. Commit evidence/docs/active-lock changes separately. Force-add this ignored brief and report. Final independent review, exact-head CI, push, and merge remain controller-owned.
