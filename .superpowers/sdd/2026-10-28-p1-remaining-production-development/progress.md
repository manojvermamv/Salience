# Planning ledger — P1 remaining production development

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
