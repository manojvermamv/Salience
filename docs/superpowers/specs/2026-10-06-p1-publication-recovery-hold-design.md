# P1 Publication Recovery Hold and Parallel Status Correction Design

**Historical scope:** This dated design preserves its original scope and evidence; its checkpoint is not current project status. Use [V4 progress](../../v4/progress.md#current-state) and [the V4 blueprint](../../v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) for the active handoff.

**Approved:** 2026-10-06

**Rollback-floor amendment approved in chat:** 2026-10-06

**Starting HEAD:** `d0311725686d81b7b4747c2d6225986e5ea3a524`

**Previously qualified publication baseline (not an `0042` rollback target):** `a4b2368cdb463939d093020224a5b5667392aee6`

## Purpose

Continue the existing V4 P1 implementation without restarting completed P0/P1 work or changing the target architecture. This slice closes the remaining local review and status defects, records the completed corrected-publication rereview durably, and adds one bounded recovery increment: a durable, operator-visible, non-resumable hold for an exact publication receipt whose outcome cannot be projected safely.

Production effects remain disabled. The fixture publication budget remains zero. RG0 and full RG1 remain `HELD`; P2–P7 remain gated, optional P6 remains disabled, and `main` remains unmerged.

This P1 hold is safety evidence only: it does not advance or reopen a cycle, select another stage or provider, invoke or authorize a platform adapter, create an analytics observation or outcome, or trigger learning, an experiment, or a release decision. This slice adds no platform connector or production authorization. Platform delivery and measurement remain later gated phases, and all evidence here remains effects-disabled fixture evidence.

## Reconciled starting state

- The effects-disabled publication fixture already rechecks the original account, baseline, approval, policy, rights, current scoped authority, exact request, and immutable receipt. An expired execution permit permits only exact receipt readback under a separate current reconciliation grant. Production-wide cross-service revocation remains unqualified.
- Published readback during ordered post-close cancellation already raises nonretryable `LegacyPublicationRecoveryHeld` before publication/cancellation status, settlement/release, output, or terminal job projection. The accepted receipt, canonical effect, job, and reservation remain under their original liability.
- The corrected-publication rereview is complete. GPT-6.1-Sol reported no Critical, Important, or Minor findings; GPT-5.6-Sol returned `ACCEPT`. The five named publication regressions were also rerun on the current tree and passed 5/5 in 19.97 seconds. The ignored review reports require a tracked durable summary and hashes.
- Cycle execution deadline ownership and case/review deadline ownership are already separated at `d031172`; no runtime ownership redesign is justified.
- Five active documentation/status references are stale. Historical item-5/6/7 captures and exact-commit CI evidence remain valid for their recorded scopes and must not be rewritten.
- `ParallelTeamStore._inspect` can report an already-started parent as `queued` while completed or unknown children wait beside queued siblings. Child lifecycle and storage are otherwise consistent, so no parallel-team migration is needed.

## Selected approach

Implement a dedicated publication recovery-hold sidecar and enrich the existing signed run inspection. Do not relax the general `v4_recovery_cases` rule that rejects new recovery cases for closed cycles. The hold records unresolved evidence and ownership but grants no action.

This approach is selected because it closes a concrete gap in the current publication path while preserving closed-cycle, permit, liability, and revocation rules. A general versioned production policy gate is broader than one bounded slice and still needs separate policy decisions. Production worker routing, live schedule inventory, drain, and rollback qualification require a real deployment and cannot be established by a local fixture.

### Rollback compatibility amendment

The retained `039ddf39cb9b846c205f352e083293fbec6f9fc5` application (runtime-equivalent to qualified A4 `a4b2368cdb463939d093020224a5b5667392aee6`) is a proven unsafe rollback target once migration `0042` has retained holds. In the exact retained-code reproduction, that worker projected an expired-receipt effect to completion and added receipt, status, checkpoint, audit, and provenance liabilities before returning its typed held failure. It remains prohibited and is retained only as a negative control.

Adopt a forward compatibility floor. The supported rollback target becomes the first independently reviewed and qualified hold-aware application whose commit is `69d7ab5da02cc9b8013f17b3e9aed3f7edb758a6` or a descendant of it in the same Git history. The floor is not established merely by naming a commit: qualification must bind its full Git SHA, source manifest, built artifact digest, and runtime import paths, then execute that actual artifact against authentic retained holds. Unknown, unpinned, unrelated-history, and pre-floor artifacts must fail closed before publication-worker registration whenever `0042` is in use. An empty hold table does not by itself authorize pre-floor code because an unrecorded expired-receipt retry may still reach the unsafe path.

Before restarting any prior supported application, including the qualified floor, rollback verification must validate **both** recovery-hold reasons (`expired_permit_receipt` and `published_during_cancel`) and compare their full canonical liability snapshots. Missing, duplicated, rewritten, reason-mismatched, or liability-mismatched evidence blocks restart. Migration `0042` and its immutable rows remain in place; populated downgrade stays prohibited. Because this increment defines no hold-retirement path, the compatibility floor remains binding until a separately reviewed migration changes that rule.

This amendment qualifies only the disposable, effects-disabled local fixture. It does not qualify a production artifact, production routing, live drain, or production rollback, and it does not release any gate.

## Deliverables

### 1. Parallel-team parent status

Derive the parent state from the ordered child states as follows:

1. `queued` only when every child is `queued`.
2. `running` when any child has left `queued` and at least one child remains `queued` or `running`. This includes completed-plus-queued and unknown-plus-queued recovery gaps.
3. When all children are terminal, retain the existing precedence:
   - `succeeded` when all children succeeded;
   - `cancelled` for an explicitly cancelled team;
   - `partial` when at least one child succeeded;
   - otherwise `failed`.

The parent canonical run remains `delegating` until all children are terminal. No new parent `unknown` or `timed_out` status is introduced; those remain child outcomes. API, SDK, and CLI continue to return the same store projection.

### 2. Durable publication recovery hold

Add `migrations/versions/0042_legacy_publication_recovery_holds.py` with revision `0042_publication_recovery_holds` after `0041_native_publication_sources`. It creates `v4_legacy_publication_recovery_holds`, an append-only sidecar with:

- stable hold ID;
- unique original `operation_id`;
- workspace, goal, intent, cycle, context, job, and publication-attempt identities;
- original actor/owner, frozen context execution deadline, and claimed permit expiration;
- exact request fingerprint and receipt fingerprint;
- exact remote ID and observed remote state;
- typed reason: `expired_permit_receipt` or `published_during_cancel`;
- immutable state fixed to `held`;
- creation time and safe evidence metadata needed for audit.

Foreign keys and a database trigger validate that every identity belongs to the same original publication-stage dispatch and its exact immutable fixture receipt. The request and receipt fingerprints use the same canonical JSON/hash rules already enforced by migration `0040`. The operation is the idempotency identity: an exact repeated observation returns the original hold; any changed binding, reason-incompatible evidence, or fingerprint conflicts and fails closed.

The sidecar does not contain a mutable resolution projection. It does not reopen the cycle, renew a permit, create a generic recovery case, enqueue a resume signal, retry or cancel a remote effect, mark a publication published/cancelled, or settle/release liability.

### 3. Existing inspection surface

`LegacyDispatch.inspect` joins the hold by the original operation and returns a bounded `recovery_hold` projection containing safe IDs, reason, state, owner, frozen execution deadline, permit expiration, and creation time. When absent, `recovery_hold` is `null`.

The existing signed `GET /v1/publications/runs/{job_id}` route remains the sole fixture publication inspection endpoint. `LegacyPublicationClient.inspect` and `content legacy-publication inspect` already pass through this response and require no new command. No hold mutation endpoint or client method is added.

## Data flow and ownership

### Expired-permit accepted-receipt recovery

1. The existing submit/reconcile activity proves it is the original activity and that the execution permit is expired rather than current.
2. It loads the submitted/completed canonical effect and exact durable fixture receipt.
3. It validates the request, fingerprints, remote identity, accepted receipt, current persisted publication authorization, and the separate current account reconciliation grant.
4. It inserts or exact-replays the recovery hold in its own committed PostgreSQL transaction.
5. It performs no second submit and no status transition.
6. It raises nonretryable `LegacyPublicationRecoveryHeld` carrying the stable hold ID in safe failure details.

### Published readback during ordered post-close cancellation

1. The pinned cancellation activity reads the exact original remote receipt.
2. If the receipt is `published`, it inserts or exact-replays the `published_during_cancel` hold before any base cancellation projection.
3. It raises nonretryable `LegacyPublicationRecoveryHeld` with the stable hold ID.
4. The accepted receipt, remote progress, canonical effect, running job, reservation, closed cycle, and original outbox history remain unchanged. No publication row or publication/cancellation status event is created.

### Authority ownership

Hold persistence is safety bookkeeping owned by the publication activity/repository after exact evidence has been validated. It does not require renewed execution authority and cannot create effect authority. Expired-permit readback still requires its separate current reconciliation grant before evidence can be observed. Run inspection still requires current `cycles:read` authority. If the original actor is revoked, the hold remains durably owned by that original identity and visible only to a currently authorized reader; revocation never deletes or resolves it.

Database unavailability or a failed insert prevents a successful recovery projection. The activity may retry the same read-and-insert operation under its existing bounded retry policy; the unique operation identity makes that retry safe. Exhaustion leaves the workflow failed/held and cannot cause a send, terminal projection, or liability release.

## Acceptance tests before implementation

Tests are written first and must fail for the missing behavior.

### Parallel status

- With `max_concurrency=1`, finish the first child while siblings remain queued; the parent is `running`, its canonical run remains `delegating`, and no `team.finished` event exists.
- After worker interruption, `unknown` plus queued siblings reports `running`; recovery claims only queued siblings with their original IDs and reaches the existing terminal aggregate.
- API/SDK/CLI pass through the same intermediate and terminal parent states.

### Recovery hold

- Each of the two triggers creates exactly one hold bound to the original identities and exact receipt.
- For **each** recovery reason, concurrent exact hold inserts for the same operation and reason converge on one row and one stable hold ID; neither caller can duplicate business events, change liability, or observe a conflicting success. Complete before/after snapshots prove the hold row and every associated receipt, publication attempt, effect, job, reservation, cycle, event, output, outbox, checkpoint, audit, and provenance liability are unchanged apart from the one first hold insertion.
- Exact activity retry/readback returns the stable hold; changed request, receipt, remote ID, operation, or reason conflicts.
- Published-after-close cancellation retains receipt/effect/job/reservation/closed-cycle state and creates no publication/status event, output, settlement/release, or extra outbox work.
- Expired-permit recovery performs receipt-only readback under the separate reconciliation grant and never performs a second submit or status poll.
- Missing or foreign receipt, missing canonical effect, revoked reconciliation authority, altered account/policy/rights facts, or inconsistent stop/binding fails before a misleading hold or business projection.
- Injected hold-transaction failure produces no false hold, terminal projection, or new effect; an exact later retry can persist the one hold.
- For **each** recovery reason, a real process crash after the hold transaction commits but before the activity reports the typed failure is recovered by exact replay of the committed hold. The retry returns the same stable hold ID, leaves exactly one hold row, and leaves every associated receipt, publication attempt, effect, job, reservation, cycle, event, output, outbox, checkpoint, audit, and provenance liability unchanged.
- Signed API, SDK, and CLI inspection expose the same safe hold projection. No public mutation path can resume, resolve, retry, cancel, settle, or release it.
- Direct SQL cannot update/delete a hold or insert a cross-workspace/cross-operation/fingerprint-mismatched row.

### Migration and rollback

- Upgrade from `0041` creates the table, constraints, triggers, and public-access revocation; existing deployment/test provisioning grants the concrete fixture runtime principal only `SELECT` and `INSERT`.
- Empty downgrade to `0041` and re-upgrade succeeds.
- Populated downgrade refuses to destroy hold history while leaving the database at `0042`.
- Before any prior supported worker is restarted, rollback qualification creates and verifies one retained hold for each reason (`expired_permit_receipt` and `published_during_cancel`), drains current workers, and proves both holds and all associated liabilities remain unchanged. A missing, duplicated, rewritten, mismatched, or liability-changing reason blocks the restart.
- The launch gate rejects `039ddf`/A4, unknown artifacts, unpinned artifacts, unrelated-history artifacts, and every pre-floor artifact before publication-worker registration. It accepts only the exact independently reviewed floor artifact, or a separately qualified compatible Git descendant, after the both-reason verifier passes.
- The actual pinned floor worker executes the retained workflows for both reasons after verification and returns the typed held outcome with zero provider submission/status polling, exactly one stable hold per operation, and byte-identical liability snapshots. A second both-reason verification passes after execution.
- Existing `0040` receipts and `0041` native publication schedule history remain byte/identity compatible.

## Migration and operational rollback

Migration is additive and does not backfill or fabricate holds for historical failures. Existing application readers continue to work because the new inspection member is additive. The migration revokes public table access. Existing deployment/test role provisioning explicitly grants the fixture runtime principal only `SELECT` and `INSERT` on the hold table; no migration-managed application role is assumed. No identity, grant, receipt, cycle, job, reservation, or publication mutation privilege is added.

Operational rollback first creates or identifies a qualified retained hold for each recovery reason, snapshots their exact receipt/publication-attempt/effect/job/reservation/cycle/event/output/outbox/checkpoint/audit/provenance liabilities, and stops/drains the compatible fixture publication worker. Before any prior supported worker may restart, the rollback verifier reads both committed hold rows directly, matches their immutable identities and reason-specific evidence, and proves every snapshotted liability is unchanged. It refuses restart if either reason is absent, rewritten, duplicated, inconsistent, or associated with changed liability.

The restart gate then authenticates the candidate application from its pinned Git SHA, source manifest, artifact digest, and runtime import paths. It rejects `039ddf`/A4 and all unknown, unpinned, unrelated-history, or pre-floor artifacts before they register a publication worker. Only the independently reviewed and qualified hold-aware floor, or a separately qualified compatible Git descendant, may restart while migration `0042` remains in use. Qualification runs the actual floor worker against both retained reasons, expects the typed held result with zero provider calls, and requires the same stable hold IDs plus byte-identical liabilities before a second verifier pass. Destructive downgrade is permitted only when the table is empty. Populated history requires a later separately reviewed migration; it is never deleted to make rollback pass.

## Documentation and durable evidence

Update only active status and current evidence:

- correct the five audited stale current references and the duplicated R18 sentence;
- state precisely that local fixture revocation is tested while production-wide cross-service revocation remains unqualified;
- preserve the corrected cycle/case gate-ownership description;
- record the clean 2026-10-06 GPT-6.1 rereview, GPT-5.6 meta-review, their report hashes, and the 5/5 targeted rerun in tracked durable evidence;
- update R18 and R32 only for the newly tested local hold/ownership slice; retain R33 stage-specific resume, R34 external alert delivery, production policy/routing/reconciliation, and full RG1 as open;
- update blueprint, progress, validation, release/status audit, source locks, and evidence hashes with actual final commands/results.

Historical item-5/6/7 evidence, the prior 3986 review capture, older exact-commit CI records, archived snapshots, and source-bound `a4b2368` qualification remain immutable historical evidence.

## Verification and review

Run, in order:

1. focused parallel-team and publication recovery-hold tests, including migration and signed API/SDK/CLI cases;
2. existing publication/native-schedule regressions and affected P1 governance/recovery tests;
3. the complete non-live test suite;
4. documentation structure, links, requirement mapping, preserved-history, JSON, migration-DAG, evidence-hash, Mermaid, dependency, compilation, and whitespace checks required by the repository;
5. an independent GPT-6.1-Sol read-only review of the exact candidate, followed by GPT-5.6-Sol meta-review; any reviewer-driven code change outside already approved implementation scope returns for user review;
6. final re-verification after any approved repair;
7. commit and push to `origin/feat/archv4-p0-foundation`, then verify both required CI jobs against that exact candidate commit.

No result is promoted from a partial run. Evidence records the exact commit, commands, exit codes, test totals, source hashes, environment boundaries, review verdicts, and CI run/job IDs.

## Expected implementation files

- `src/salience/agents/parallel_store.py`
- `src/salience/cycles/legacy_publication.py`
- `src/salience/cycles/legacy_publication_runtime.py`
- `src/salience/cycles/legacy_dispatch.py`
- `migrations/versions/0042_legacy_publication_recovery_holds.py`
- `tests/integration/test_durable_parallel_teams.py`
- `tests/integration/test_v4_legacy_publication.py`
- `tests/integration/test_v4_migrations.py`
- existing signed API/SDK/CLI tests where the inspection projection is already exercised
- current V4 blueprint, progress, matrix, validation, release/status, evidence, and documentation-audit files

Implementation may use a focused repository/helper module if tests show that placing hold persistence in the current publication modules would duplicate transaction or validation logic. Such extraction must remain internal and may not add a new service, queue, scheduler, policy engine, or public endpoint.

## Completion boundary

This slice is complete only when the status correction, durable non-resumable hold, both-reason rollback verification, floor-artifact qualification, concurrent-insert and post-commit-crash matrices for both reasons, tracked rereview closure, active documentation reconciliation, focused/full/documentation verification, independent final review/meta-review, exact feature-commit push, and exact-commit CI all pass. It advances only the locally tested fixture portions of R18 and R32. It does not release production, RG0, RG1, R33, R34, P2–P7, optional P6, or `main` integration.
