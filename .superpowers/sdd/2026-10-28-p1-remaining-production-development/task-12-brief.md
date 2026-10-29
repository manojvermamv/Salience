# Task 12 brief — final independent-review corrections and CI evidence lock

Base commit: `a87141005796327d17a258df6549929e7a5625df`

## Outcome

Resolve the four Important findings from the final GPT-6.1 read-only review, preserve all existing safety boundaries, and leave one committed source candidate ready for exact artifact/evidence regeneration. Production effects remain disabled; RG0/full RG1 remain `HELD`; P2 remains `BLOCKED`; no live/provider/production target is contacted.

## Required red-first corrections

1. **Stop-first schedule pause.** A committed workspace or goal stop must block activation/poll/resume yet still permit the exact bound schedule's narrow emergency `pause` plus describe/readback/drain path. It must work for a pending cutover and for re-pausing an out-of-band unpause after activation. Bind the pause-only path to the exact current stop revision, schedule binding, operator/grant, route fence, target, and immutable command key. It must not authorize schedule creation, resume, polling, activation, provider effects, or a different schedule/target. Add red tests for pending-stop pause and active-cutover re-pause, plus denials for missing stop or mismatched binding/route as needed.

2. **Rollback claim fencing.** Migration 0051 must accept distinct, correctly bound issue and claim route-fence decision UUIDs. Validate the claim decision against its exact immutable `production_permit_claimed` event and validate the current pointer against that claim. Do not require the claim decision UUID to equal the permit issue decision UUID. Add a real production issue-and-claim rollback inventory regression that proves neither claim mismatch blocker appears; retain corruption denials.

3. **Rollback stop receipt cardinality.** Migration 0051 must accept canonical `CycleGovernance.set_stop` histories in which revision 1 has zero or more same-state `stopped=false` no-op commands, and later revisions have one state-changing receipt plus any number of same-state no-op commands. Historical enrollment binding must prove an unstopped state at its bound revision; current rollback fencing must prove the current scope is stopped and has at least one matching state-changing receipt/actor, without requiring exactly one command at the revision. Add red regressions using the real command path at bound revision 1, a historical unstopped revision, and an active stopped revision. Preserve drift/corruption blockers and the irreversible 0051 downgrade behavior.

4. **Durable retry timing on restart.** Before a new alert send after prior attempts, load and validate the exact outstanding `retry_scheduled` event for the same alert/event key and next attempt. Reconcile an existing local/provider receipt first; otherwise honor the remaining persisted `retry_at` delay using the injected clock/wait, reauthorize, and send with the same idempotency key. Missing, malformed, conflicting, or policy-inconsistent retry evidence must fail closed. Add a crash/restart regression showing attempt 2 cannot send before stored `retry_at`, and can send once due.

## Migration and compatibility ruling

0051 is the current development-only, effects-disabled head and has not been externally qualified. Correct 0051 in place, keep revision ID `0051_p1_recovery_rollback_v2`, keep its downgrade fail-before-mutation behavior, and test a fresh `0050 -> 0051` upgrade. Do not downgrade or mutate an existing shared database.

## Verification and report

- Run each new test against the pre-fix code and record the intended RED.
- Run focused schedule, recovery/alerts, rollback/migration, and policy/revocation suites serially on disposable local infrastructure.
- Run compile/static/diff checks.
- Write `.superpowers/sdd/2026-10-28-p1-remaining-production-development/task-12-report.md` with exact commands, exits, counts, durations, design decisions, and cleanup.
- Commit source/tests/migration/report. Do not regenerate the launch lock, evidence package, or active `docs/v4/evidence-lock.json`; the controller owns exact-candidate regeneration after review.
- Do not push or merge.
