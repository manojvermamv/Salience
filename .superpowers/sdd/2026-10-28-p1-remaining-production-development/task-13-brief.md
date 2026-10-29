# Task 13 brief — final exact candidate, active evidence lock, and release evidence

Exact implementation source commit: `8d26aa1` (resolve to full commit/tree before generation).

## Outcome

Build and measure one final development-only authenticated candidate from the exact Task 12 source, refresh the active application evidence lock required by CI, refresh the synthetic held acceptance package and durable reports, and leave one clean committed head ready for final independent reviews and push.

Production effects remain disabled. All five RG0 and seventeen RG1 rows remain `NOT RUN`/`HELD`; final audit remains `NOT RUN`; disposition remains `NO_GO`; P2 remains `BLOCKED`; `main` remains unmerged. No live/provider/production database/remote schedule/alert target is contacted.

## Sequence

1. Build the development wheel twice from exact source `8d26aa1` with the established deterministic procedure; require identical SHA-256. Regenerate only the tracked development v2 lock and companion for that exact source. Preserve the v1 lock byte-for-byte and keep external trust unprovisioned. Commit the two v2 generated files as a separate artifact commit whose parent is the exact source commit.
2. On that exact artifact commit, run the seven focused non-live groups serially with unique local Postgres/Temporal infrastructure: route/launcher, migrations 0047–0051 plus corrected rollback inventory, schedule/native, complete recovery/alerts, rollback-v2 handoff, policy/revocation, and external-acceptance checker.
3. Run the full non-live suite exactly once after the focused groups pass. Do not rerun a passing full suite unless a source/artifact correction makes its result stale.
4. Refresh `docs/v4/evidence-lock.json` as the active current application/source lock. Preserve historical lock objects and immutable historical evidence, but set the current application commit/scope to exact source `8d26aa1`, hash the complete current checker application set (all existing non-application boundary paths plus the current `src`, `tests`, `migrations`, and `scripts` file set), and make both normal and self-test `docs/v4/check.py` pass. Do not weaken the checker or workflow.
5. Refresh `docs/v4/p1-remaining-production-development-evidence.json`, Task 11/final reports, current progress/status docs, Task 12/13 ledger status, and the active documentation-status wrapper with actual hashes/results. Avoid self-hashing: source and artifact commits are immutable predecessor commits; the later evidence commit may identify them but never its own hash. Keep old attempt history factual and append the final result.
6. Run compileall, `docs/v4/test_checks.py`, `docs/v4/check.py --self-test`, normal checker, five shell documentation contracts, AgentTube reference checks used by CI, Archify validation, acceptance checker, and `git diff --check`. Commit the evidence/docs/active-lock refresh separately. Leave the tree clean.

## Required report

Write `.superpowers/sdd/2026-10-28-p1-remaining-production-development/task-13-report.md` with exact source/artifact commits and trees, wheel/lock/route/package digests, commands, exits, counts, durations, fixture scope, active-lock file count, cleanup, and all limitations. Force-add this ignored brief/report. Do not push or merge; controller owns final Luna and GPT-6.1 read-only reviews, push, and exact-head CI.
