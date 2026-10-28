# Final review fix wave report

Date: 2026-10-28

## Changes

- Routed worker shutdown now produces a route-bound `WorkerDrainReceipt`. The boundary is captured immediately before requested shutdown, or after the run task completes naturally. The drain readback must be at or after that boundary and no later than its validation time. Slot transfer requires a receipt bound to the exact route, slot, owner, epoch, manifest, artifact lock, and route tuple; its fresh readback is checked against the same time interval before `release_slot` can run.
- Recovery alert escalation now uses the immutable initial alert intent time plus `escalation_after[level - 1]`. Repository request creation and alert delivery share the same due-time function and each take an injected clock. Level 0 maps to `owner`; every configured positive level maps to `on_call` and requires at least one approved on-call group. Delivery rechecks intent identity, policy evidence, tier, and due time before target lookup or send. Existing per-level event identity and provider idempotency keys remain in use.
- No provider, production database, production schedule, live alert, RG0/RG1, P2, or Task 11 artifact/evidence path was exercised or changed. Alert tests used the local disposable Compose database and an in-process fake target. Routed-worker tests used the in-process route authority and Temporal readback fakes.

## Red evidence

The worker regression tests failed against the old release path as expected: stale pre-boundary and future-dated zero-work readbacks both reached release instead of raising. Command exit: 1, 2 failed.

```sh
TEST_DATABASE_URL=postgresql://localhost/salience TEST_TEMPORAL_TARGET=127.0.0.1:7233 PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_p1_production_routing.py::test_route_transfer_rejects_stale_pre_drain_readback_without_release tests/integration/test_p1_production_routing.py::test_route_transfer_rejects_future_readback_without_release
```

The alert regression tests failed against the old request path as expected: early escalation was accepted, due escalation remained on the owner tier, and escalation without an on-call group was accepted. Command exit: 1, 3 failed.

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_p1_production_recovery.py::test_alert_request_rejects_escalation_before_policy_due tests/integration/test_p1_production_recovery.py::test_due_escalation_routes_to_on_call_and_replays_same_delivery tests/integration/test_p1_production_recovery.py::test_escalation_without_configured_on_call_group_is_rejected_before_intent
```

## Green evidence

The full routed-worker contract module passed: exit 0, 29 passed.

```sh
TEST_DATABASE_URL=postgresql://localhost/salience TEST_TEMPORAL_TARGET=127.0.0.1:7233 PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_p1_production_routing.py
```

The full recovery contract module passed: exit 0, 45 passed in 310.10 seconds. After expanding the escalation test to cover levels 1 and 2, exact due boundaries, send-time revalidation, and replay, the three alert regressions passed again: exit 0, 3 passed in 20.41 seconds.

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_p1_production_recovery.py
PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_p1_production_recovery.py::test_alert_request_rejects_escalation_before_policy_due tests/integration/test_p1_production_recovery.py::test_due_escalation_routes_to_on_call_and_replays_same_delivery tests/integration/test_p1_production_recovery.py::test_escalation_without_configured_on_call_group_is_rejected_before_intent
```

The focused worker boundary set passed: exit 0, 8 passed. It covers stale/future rejection, equality acceptance, receipt identity rejection, shutdown boundary capture, run error drain, hung drain, and release behavior.

```sh
TEST_DATABASE_URL=postgresql://localhost/salience TEST_TEMPORAL_TARGET=127.0.0.1:7233 PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_p1_production_routing.py::test_route_transfer_rejects_stale_pre_drain_readback_without_release tests/integration/test_p1_production_routing.py::test_route_transfer_rejects_future_readback_without_release tests/integration/test_p1_production_routing.py::test_route_transfer_accepts_readback_at_both_boundary_equalities tests/integration/test_p1_production_routing.py::test_route_transfer_rejects_receipt_identity_mismatch_without_release tests/integration/test_p1_production_routing.py::test_route_renewal_loss_drains_before_expiry_and_preserves_identity tests/integration/test_p1_production_routing.py::test_route_run_error_awaits_async_shutdown_before_readback tests/integration/test_p1_production_routing.py::test_hung_route_drain_fails_closed_at_lease_expiry_without_transfer tests/integration/test_p1_production_routing.py::test_restart_preserves_workflow_cycle_and_operation_identity
```

Final static checks passed: `git diff --check` exit 0; `.venv/bin/python -m compileall -q` over all six changed source and test files exit 0. No Ruff or Black executable is installed in `.venv`.

## Runner setup notes

- System `pytest` was unavailable: the initial `pytest -q ...` command exited 127 (`pytest: command not found`).
- The first `.venv/bin/python -m pytest ...` attempt omitted `PYTHONPATH=src`; pytest session setup started the local Compose test services, then Alembic exited 1 because it could not import `salience` (pytest process exit 3). The corrected commands above use `PYTHONPATH=src`. This setup failure ran no test cases and did not touch a production database.

## Follow-up review correction: replay after clock rollback

The scoped rereview found that delivery-time enforcement ran during immutable intent validation, before either the committed local receipt or provider same-key receipt could be replayed. This made an already accepted escalation appear not due if the injected clock moved backward after delivery. The fix preserves work, intent, policy-evidence, recipient-tier, and on-call configuration validation before any replay; it then returns exact committed local receipts or reconciles an exact provider readback before consulting the mutable clock. The configured due boundary is still enforced before each new target send attempt, and alert-request creation gating is unchanged.

Two injected-clock regressions were added for positive escalation level 1: replay of a committed local receipt after moving the clock one microsecond before due, and crash recovery where the provider accepted but the local receipt append failed, followed by clock rollback before same-key lookup. Both failed before the production change at the expected `production alert escalation is not due` check: exit 1, 2 failed, 45 deselected in 14.15 seconds.

```sh
PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_p1_production_recovery.py -k 'committed_escalation_receipt_replays_after_clock_rolls_back or provider_acceptance_reconciles_after_clock_rolls_back'
```

The alert-name focused selection passed: exit 0, 5 passed, 42 deselected in 28.33 seconds. The combined alert and explicit replay-regression selection passed: exit 0, 7 passed, 40 deselected in 41.78 seconds.

```sh
PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_p1_production_recovery.py -k 'alert or committed_escalation_receipt_replays_after_clock_rolls_back or provider_acceptance_reconciles_after_clock_rolls_back'
```

The full recovery module passed serially: exit 0, 47 passed in 323.74 seconds. Compile validation and `git diff --check` both exited 0. These checks used the local disposable Compose database and in-process fake alert target; no provider or production path was used.

```sh
PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_p1_production_recovery.py
.venv/bin/python -m compileall -q src/salience/cycles/_production_recovery_alerts.py tests/integration/test_p1_production_recovery.py
git diff --check
```

## Exact-candidate Task 11 confirmation — 2026-10-28

The reviewed alert replay correction is included in exact implementation source `b4865d115c225c828e54777024f1de726fa4cd86` and was measured with generated artifact commit `3c62a4f01e974d97225100bb2afc3a3665ef601d`. The complete recovery module passed 47 tests, including both committed-receipt and provider-acceptance replay cases after clock rollback. Policy/revocation passed 124 cases, and the single complete non-live suite passed 1,129 tests with 26 historical Task 8 fixture skips and three Alembic configuration deprecation warnings. The local disposable PostgreSQL and Temporal project was removed after the run.

This confirms the fix against local contracts and does not establish provider, alert-target, production-database, or external route behavior. The exact synthetic package continues to hold all RG0/RG1 rows, leaves all identities unverified, disables effects, and reports `NO_GO`/`UNCHANGED`. The detailed commands and digests are in [`task-11-report.md`](task-11-report.md) and [`final-evidence-report.md`](final-evidence-report.md).

The source/package consistency check found the current source tree, generated lock, wheel, image, route tuple, authenticated route-manifest digest, and migration head agree across the lock and evidence package. No Critical or Important mismatch was found in that local scope. This was not an independent rereview; the parent agent's scoped rereview remains pending.

The active documentation metadata was also currentized for the final exact-source records. `docs/v4/test_checks.py` passes 25/25 after restoring the 300-path/125-Markdown inventory and the append-only progress digest. The normal docs validator modes still return 1 against the preserved earlier application evidence lock; no historical lock or audit source was altered. Detailed command outcomes are in `task-11-report.md`.
