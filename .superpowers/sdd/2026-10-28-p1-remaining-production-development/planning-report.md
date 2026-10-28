# Planning report — P1 remaining production development

Planning baseline: `arch` at `0763702bbedda7ffc99c3b526a5d018340e63aae`.

The remaining work is specified as 11 independently reviewable tasks in safe dependency order. The plan extends the actual migration head 0046 additively with revisions 0047–0050 while preserving 0043 as the historical fixture floor. It resolves route identity by retaining the stable UUID already used by code and migration 0044, then authenticating a canonical `route_tuple_sha256` alongside it. The implementation reuses the existing worker launcher, compatibility gate, `ProductionEffectDispatch`, schedule cutover state machine, and typed recovery cases.

The plan adds production recovery work/events and schedule bindings, injected route/schedule/provider-read/alert contracts with deterministic fakes, a typed same-operation no-effect reissue command, complete rollback verifier v2, and a local checker for the exact 22-row external evidence package. The Slice 2 progress/evidence and frozen initial gap audit remain historical records.

Production remains disabled. The five RG0 checks and 17 RG1 rows stay `NOT RUN`/`HELD`; RG0/full RG1 remain `HELD`; P2 remains `BLOCKED`; `main` remains unmerged. External target identity, named owners, credentials, route lease service, provider/account, Temporal inventory, alert destination, operations limits, and independent production review remain prerequisites.

The plan's requirements trace maps all 17 Slice 2 policy/revocation cases, seven routing cases, twelve schedule cases, thirteen recovery cases, and twenty external checker cases to implementation tasks. Local checker/fake results remain development evidence. Named-target acceptance and the final exact-candidate/environment audit remain external and `NOT RUN`/`HELD`.

The self-review made four interface rulings explicit: canonical route tuple list ordering; scoped inventory versus single-schedule pause/resume; `ProductionRecoveryAlerts` implementing the schedule hold-alert sink; and separate route-authority and complete Temporal worker readbacks for rollback verification. It also preserves the typed recovery event as the one-use command receipt consumed by migration 0049, with no reclassification of prior Slice 2 fixture evidence.

The initial docs test run found that the current documentation inventory lacked the newly added plan/ledger paths and the audit-review wrapper had no current-text digests for the updated specs. The fix was append-only: prior review digests/verdicts and the frozen gap-audit JSON/report remain unchanged, while a distinct 2026-10-28 currentization hash note and current artifact inventory were added.

Final documentation validation passed: `python3 docs/v4/test_checks.py` (25 tests); `python3 docs/v4/check.py --self-test` and normal mode (75 requirements, 376 evidence files, 360 inventory files, 110 Markdown files, 4 HTML files; 6 negative controls); all five `tests/scripts/*.sh` contracts; JSON parsing for the modified status inventory; and `git diff --check` for staged and unstaged changes. No application, migration, provider, production, or live-target tests were run. One local documentation commit has been created; its SHA is reported in the handoff. No push or merge is in scope.
