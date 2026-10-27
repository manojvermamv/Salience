# P1/RG0/RG1 Initial Residual-Gap Audit Plan

> **Spec authority:** `docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md`, Slice 1, plus the user's 2026-10-27 instruction to audit first and draft only increments proven necessary.

**Goal:** Bind one initial P1/RG0/RG1 residual-gap audit to exact commit `7c1ddcda280b510048ad61106c406171edd64f18` and one named no-effects host environment, decide whether each of the five conditional increments is required, then write and independently evaluate only the required bounded design specifications.

**Scope:** Documentation, evidence, and design contracts only. No application implementation, schema migration, production credential, live provider call, live schedule mutation, production effect, gate release, P2 work, `main` merge, or historical evidence rewrite is permitted.

**Global constraints:**

- Freeze the audit source at `7c1ddcda280b510048ad61106c406171edd64f18`; later documentation commits may record conclusions but cannot silently become source evidence.
- Identify the inspected environment by a secret-free manifest and digest. Treat absent production configuration, owners, accounts, schedules, alerts, and operator evidence as `NOT RUN` or `HELD`, never as `PASS`.
- Reconcile all five RG0 rows (`R01`, `R22`, `R69`, `R16F`, `R23F`) and all 17 P1/RG1 rows in `p1-qualification-matrix.json`.
- Every row records owner, exact candidate/artifact/environment, evidence scope, `PASS`/`FAIL`/`NOT RUN`/`HELD`, stale status, remaining dependency, and the bounded increment that owns any gap.
- Preserve fixture, production, CI, formal approval, and historical-candidate scopes separately.
- Any required increment gets one focused design file with acceptance tests, migration/backfill behavior, rollback/restart fences, observability, owners, evidence contract, and explicit exclusions.
- Any `FAIL`, `NOT RUN`, `HELD`, stale evidence, missing owner, or environment mismatch keeps Slice 7 and P2 blocked.

---

## Task 1: Freeze the source and environment

**Files:**

- Create: `docs/v4/p1-rg0-rg1-initial-gap-audit.json`
- Create: `docs/v4/p1-rg0-rg1-initial-gap-audit.md`

1. Record the exact Git commit, branch, remote-ref readback, clean/dirty state, UTC capture time, host identity, OS/architecture, Python, Docker/Compose versions, running local service identities, production-related environment-key presence as booleans, effects state, and a deterministic environment-manifest digest.
2. Record exact-head Actions run `40443346020` separately from the frozen application candidate `182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f` and its local qualification.
3. State that the environment is an effects-disabled development host with local PostgreSQL/Temporal fixtures and no production target configuration; it cannot supply external RG0/RG1 acceptance.
4. Verify the artifact parses and all identifiers reproduce from read-only commands.

## Task 2: Reconcile every applicable row

**Files:**

- Modify: `docs/v4/p1-rg0-rg1-initial-gap-audit.json`
- Modify: `docs/v4/p1-rg0-rg1-initial-gap-audit.md`
- Modify: `docs/v4/p1-qualification-matrix.json`
- Modify: `docs/v4/p0-release.json`

1. Map current code, tests, migrations, source locks, evidence artifacts, reviews, CI, and external records to the five RG0 and 17 P1/RG1 rows.
2. For each row, distinguish local capability from production acceptance and current evidence from stale or earlier-candidate evidence.
3. Assign one accountable owner role and one next dependency. Missing named human or production environment remains explicit.
4. Run a machine check for unique row IDs, allowed statuses, complete fields, valid evidence paths, exact commit/environment binding, and no P2-go claim.

## Task 3: Decide the five conditional increments

**Files:**

- Modify: `docs/v4/p1-rg0-rg1-initial-gap-audit.json`
- Modify: `docs/v4/p1-rg0-rg1-initial-gap-audit.md`

For each candidate increment, record `REQUIRED` or `NOT_APPLICABLE`, its exact owning rows, source-backed reason, prerequisites, and why existing fixture evidence is or is not sufficient:

1. Production policy and cross-service revocation.
2. Production worker routing, registration, drain, and artifact fencing.
3. Live Temporal schedule inventory, cutover, stop, and rollback fencing.
4. Production recovery workers, remote-read reconciliation, and external alerts.
5. External RG0/RG1 acceptance evidence.

The initial audit may define work but cannot declare RG0/RG1 entry conditions passed.

## Task 4: Draft each required bounded design

**Files (create only when Task 3 says `REQUIRED`):**

- `docs/superpowers/specs/2026-10-13-p1-production-policy-and-revocation-design.md`
- `docs/superpowers/specs/2026-10-13-p1-production-routing-and-worker-fencing-design.md`
- `docs/superpowers/specs/2026-10-13-p1-live-schedule-fencing-design.md`
- `docs/superpowers/specs/2026-10-13-p1-production-recovery-and-alerts-design.md`
- `docs/superpowers/specs/2026-10-13-rg0-rg1-external-acceptance-design.md`

For each required spec:

1. Define intent, mapped rows, entry dependencies, owners, trust boundaries, interfaces, state transitions, failure behavior, and excluded future-phase behavior.
2. Specify red-first acceptance tests, concurrency/crash/restart cases, exact-candidate/environment qualification, and negative controls.
3. Specify additive migration/backfill only where durable new facts are essential; preserve immutable history and unknowns.
4. Specify rollback verification, including liabilities, remote uncertainty, compatible worker/schedule floors, and conditions for restarting prior code.
5. Require independent review and durable evidence without granting production authority.

## Task 5: Reconcile navigation and gate status

**Files:**

- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/v4/IMPLEMENTATION-BLUEPRINT.md`
- Modify: `docs/v4/progress.md`
- Modify: `docs/v4/validation.md`
- Modify: `docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md`
- Modify: `docs/v4/evidence-lock.json`
- Modify: `docs/v4/documentation-status-audit.json`

1. Mark Slice 1 as completed only at its frozen source commit/environment.
2. Link every required spec and mark implementation/qualification pending; do not mark conditional slices complete.
3. Record Slice 7 and P2 as blocked by every non-PASS, stale, missing-owner, or environment-mismatched row.
4. Recompute evidence/document inventories using the repository process; do not edit archived historical captures.

## Task 6: Multi-step audit, repair, and specification evaluation

1. Run an independent row-by-row audit of the initial report against source/evidence.
2. Run an independent cross-spec consistency review for ownership, dependency cycles, authority leaks, duplicate architecture, migration coupling, rollback gaps, and untestable acceptance language.
3. Repair every confirmed Critical/Important finding and re-review the exact fixes.
4. Run a final independent whole-change review and score every spec for requirement traceability, boundedness, testability, migration safety, rollback/restart safety, observability, and gate honesty.
5. Run documentation self-tests, normal documentation checks, JSON/hash integrity, link checks, Mermaid checks for changed diagrams, whitespace checks, and focused schema checks for the new audit/spec artifacts.
6. Commit the reviewed documentation candidate. Push and exact-head CI remain a final branch-publication action; CI evidence is reported against that exact pushed commit rather than creating a self-invalidating post-CI commit.

## Exit

The work is complete when the frozen initial audit is reproducible, the five increment decisions are explicit, every required bounded spec passes independent evaluation, navigation/evidence records agree, and P2 remains blocked unless a later Slice 7 audit finds every applicable row current and `PASS` on one unchanged candidate/environment.


## Recorded results and current status — 2026-10-27

**Initial audit/design checkpoint:** COMPLETE_REVIEWED for frozen source `7c1ddcda280b510048ad61106c406171edd64f18` and environment `18b15955b9a3be547bd1998ac2c1fce759c99d647fc3eeaa0ea21858e7a81436`; five increments REQUIRED; five designs COMPLETE_REVIEWED; implementation and target qualification NOT RUN; 22 final-entry rows remain non-PASS; RG0/RG1 HELD; Slice 7/P2 BLOCKED; production disabled; main unmerged.

Tasks 1–4 and their scoped review/fix loops are complete: the frozen audit found five REQUIRED increments, and the five bounded designs passed row-by-row and cross-spec review. The current documentation status inventory contains 283 eligible artifacts, including three byte-preserved whole-change review reports; the 366-file application evidence lock is unchanged. The whole-change documentation review is PASS_AFTER_FIX_ROUND_1 for candidate manifest `cafa0bd46ede2cc8aef51058f46541068a07cd031774c8b5501270bc2385ee9a`: its initial result was FAIL (0 Critical / 3 Important / 0 Minor), the bounded F1–F3 documentation/evidence fixes were rereviewed PASS with 0 new Critical / 0 new Important. This scoped documentation verdict is distinct from the final exact-candidate/environment P1/RG0/RG1 audit, which remains NOT RUN and BLOCKED until required increments are implemented and current external RG0/RG1 evidence exists for one frozen final candidate/environment. The orchestrator meta-review still accepts the initial audit/spec design evaluation only. Implementation, live qualification, and external RG0/RG1 acceptance remain NOT RUN.

The final audit and P2 remain BLOCKED by the 22 non-PASS rows, absent named owners, and missing target evidence. Production remains disabled; RG0/RG1 remain HELD; `main` remains unmerged. See the [portable review record](../../v4/p1-rg0-rg1-audit-and-spec-review.md).
