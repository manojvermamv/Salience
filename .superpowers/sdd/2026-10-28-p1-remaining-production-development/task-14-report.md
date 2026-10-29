# Task 14 report — final review corrections

Date: 2026-10-29
Status: the initial four source corrections and the subsequent recipient-tier recovery correction are implemented and focused verification complete; exact-candidate artifact/evidence refresh remains controller-owned.

## Source changes

Initial source commit: `becb3951d96f395b2c3ec9303e578dfb1e7eda5d`
Tree: `3d9bd870d2a46b805106602cfcacb92bbba7b55a`

1. Routed worker renewal now requires route ID, slot ID, owner ID, fencing epoch, route-manifest digest, and artifact-lock digest to remain identical to the last trusted lease. Expiry can advance. A mismatch is lease loss, and drain/readback continues with the last trusted identity.
2. Alert restart now looks up the same provider key before retry-history handling. A committed latest attempt without its retry event is treated as a possible post-commit crash: the exact attempt is validated, its claim TTL elapses, the provider is looked up again, and current policy/owner authorization is checked before retrying the same attempt/key. No duplicate attempt event is appended. Conflicting prior history still fails closed, and existing retry limits, receipt reconciliation, and held behavior remain intact.
3. Migration 0051 now uses an exact permit-issue payload validator parallel to its claim validator. It checks the complete canonical payload including nulls and optional publication binding, counts issue events, rejects unknown/omitted fields, and bounds typed `database_time` from event creation through permit `issued_at`.
4. Migration 0051 now revalidates publication-hold reason against current cycle state/disposition and permit expiry, exact liability snapshot and remote receipt binding, current dispatch/permit/enrollment rows, and the original 0047 source fingerprint material. Reason, stored fingerprint, or source-row drift adds `production_publication_hold_source_mismatch` while preserving the existing liability-change blocker and held semantics.

## Red/green evidence

Integration tests used local Postgres and Temporal endpoints with repository `PYTHONPATH`; no provider or production target was used. Focused RED results reproduced the findings:

- Route renewal identity regression: a valid alternate slot/owner was accepted and a changed fencing epoch reached readback under the wrong identity. Invalid digest cases were already rejected by existing validation.
- Alert crash-window regressions: restart classified the committed attempt without a retry event as corruption. A separate owner-timing regression failed because an unavailable owner caused an immediate hold before the committed attempt's claim TTL elapsed.
- Permit-issue payload drift selector, `-k 'rejects_permit_issue_event_drift'`: **4 failed**, all at the missing issue-event blocker assertion (omitted scope, extra field, malformed time, future time).
- Publication-hold reason/source selector, `-k 'revalidates_production_hold_reason_and_source'`: **6 failed**, all at the missing source-mismatch blocker assertion (both reasons × reason swap, fingerprint mutation, source-row mutation).

Final focused GREEN commands and results:

```text
tests/integration/test_p1_production_routing.py -k 'route_renewal_identity_mismatch'
4 passed, 29 deselected

tests/integration/test_p1_production_recovery.py -k 'alert_restart_after_attempt_commit_waits_for_claim_ttl_and_reuses_attempt or alert_restart_reconciles_receipt_that_appears_during_claim_ttl or alert_restart_waits_for_claim_ttl_when_latest_retry_record_is_missing or alert_restart_after_attempt_crash_preserves_bounded_retry_schedule or alert_restart_holds_after_unavailable_final_attempt_reuses_committed_attempt or alert_restart_rechecks_unavailable_owner_after_committed_attempt_claim_ttl or alert_restart_rejects_invalid_persisted_retry_evidence or alert_restart_waits_for_persisted_retry_at_before_sending'
12 passed, 55 deselected

tests/integration/test_p1_production_recovery.py -k 'alert_delivery_and_acknowledgment or due_escalation_routes_to_on_call_and_replays_same_delivery or owner_and_outage_escalation'
3 passed, 64 deselected

tests/integration/test_p1_production_policy_revocation.py -k 'rejects_claim_event_payload_drift or rejects_permit_issue_event_drift or binds_distinct_issue_and_claim_decisions'
13 passed, 124 deselected

tests/integration/test_p1_production_recovery.py -k 'revalidates_production_hold_reason_and_source or rejects_snapshot_or_liability_change or checks_zero_one_and_many_rows_for_each_reason'
8 passed, 59 deselected
```

Touched Python files compile with `.venv/bin/python -m compileall -q` over the six changed source/test/migration files. `git diff --check` passed. No broad or full suite was run, as required for this correction task.

## Evidence boundary

Task 13's artifact, synthetic acceptance package, and active evidence lock remain historical and still identify source `8d26aa15`; they were not rebuilt or edited here. The controller will generate a new exact candidate after review. The synthetic package remains `NO_GO`; production effects remain disabled; RG0 and full RG1 remain `HELD`; P2 remains `BLOCKED`; external qualification remains `NOT RUN`; and no push, merge, or live-system contact occurred.

## Luna follow-up — committed alert recipient tier — 2026-10-29

- Source/test commit: `2dc31a0d0b0652ad01c62f57ac7351b335eb66d0`; tree `4025192ce4139063ed59070a59cfe17c24575983`.
- Pending-attempt recovery now reauthorizes and resends the exact recipient request recorded on the committed attempt. A pending on-call attempt stays on-call if the owner returns. When a committed owner attempt resumes after the owner became unavailable, recovery records owner-unavailable evidence and persists the retry boundary before advancing to on-call for the next attempt; final attempts and profiles without backup hold without resending the committed owner attempt under a different tier. Provider idempotency keys and bounded attempt numbers remain unchanged.
- Retry-history validation now permits only the backed owner-to-on-call transition, requiring exact owner-unavailable evidence within that attempt/retry interval. The retry's tier must match the next attempt where present; other tier drift and conflicting evidence fail closed.
- Focused red command: `PYTHONPATH=src TEST_DATABASE_URL=postgresql://salience:salience@172.20.0.2:5432/salience TEST_TEMPORAL_TARGET=172.20.0.3:7233 .venv/bin/pytest -q tests/integration/test_p1_production_recovery.py -k 'preserves_committed_on_call_tier_when_owner_returns or changes_owner_tier_only_at_authorized_retry_boundary or holds_instead_of_changing_pending_owner_attempt_tier or rejects_invalid_persisted_retry_evidence'` — **4 failed, 5 passed, 63 deselected**. The failures reproduced owner-to-on-call in-place resend, on-call-to-owner reauthorization, missing final-attempt hold, and acceptance of an unbacked recorded-tier transition.
- Focused green command with the same selector: **9 passed, 63 deselected** in 63.07 seconds. Adjacent claim-TTL, receipt reconciliation, bounded retry, final-hold, owner-unavailable, and retry-corruption selectors: **12 passed, 60 deselected** in 83.66 seconds. Existing delivery/acknowledgment/on-call escalation controls: **3 passed, 69 deselected** in 22.21 seconds. Python compileall and `git diff --check` passed.
- No broad/full suite, artifact/evidence-lock rebuild, live target, push, or merge was performed. Task 13 artifacts remain historical; a new exact candidate is controller-owned. Production remains disabled; the synthetic package is `NO_GO`; RG0/full RG1 are `HELD`; P2 is `BLOCKED`; external qualification is `NOT RUN`.
