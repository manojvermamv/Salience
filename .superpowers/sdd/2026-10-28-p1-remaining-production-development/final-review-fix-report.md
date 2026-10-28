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
