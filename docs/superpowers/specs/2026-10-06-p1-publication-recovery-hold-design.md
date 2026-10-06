# P1 Publication Recovery Hold and Parallel Status Correction Design

**Approved:** 2026-10-06

**Starting HEAD:** `d0311725686d81b7b4747c2d6225986e5ea3a524`

**Qualified publication application:** `a4b2368cdb463939d093020224a5b5667392aee6`

## Purpose

Continue the existing V4 P1 implementation without restarting completed P0/P1 work or changing the target architecture. This slice closes the remaining local review and status defects, records the completed corrected-publication rereview durably, and adds one bounded recovery increment: a durable, operator-visible, non-resumable hold for an exact publication receipt whose outcome cannot be projected safely.

Production effects remain disabled. The fixture publication budget remains zero. RG0 and full RG1 remain `HELD`; P2–P7 remain gated, optional P6 remains disabled, and `main` remains unmerged.

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

Add migration `0042_legacy_publication_recovery_holds` after `0041_native_publication_sources`. It creates `v4_legacy_publication_recovery_holds`, an append-only sidecar with:

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
- Concurrent exact hold inserts for the same operation and reason converge on one row and one stable hold ID; neither caller can duplicate business events, change liability, or observe a conflicting success.
- Exact activity retry/readback returns the stable hold; changed request, receipt, remote ID, operation, or reason conflicts.
- Published-after-close cancellation retains receipt/effect/job/reservation/closed-cycle state and creates no publication/status event, output, settlement/release, or extra outbox work.
- Expired-permit recovery performs receipt-only readback under the separate reconciliation grant and never performs a second submit or status poll.
- Missing or foreign receipt, missing canonical effect, revoked reconciliation authority, altered account/policy/rights facts, or inconsistent stop/binding fails before a misleading hold or business projection.
- Injected hold-transaction failure produces no false hold, terminal projection, or new effect; an exact later retry can persist the one hold.
- A process crash after the hold transaction commits but before the activity reports the typed failure is recovered by exact replay of the committed hold. The retry returns the same hold ID and leaves the receipt, effect, job, reservation, cycle, status events, output, and outbox liabilities unchanged.
- Signed API, SDK, and CLI inspection expose the same safe hold projection. No public mutation path can resume, resolve, retry, cancel, settle, or release it.
- Direct SQL cannot update/delete a hold or insert a cross-workspace/cross-operation/fingerprint-mismatched row.

### Migration and rollback

- Upgrade from `0041` creates the table, constraints, trigger, and required narrow application-role privileges.
- Empty downgrade to `0041` and re-upgrade succeeds.
- Populated downgrade refuses to destroy hold history while leaving the database at `0042`.
- Before any prior-code worker is restarted, rollback qualification creates and verifies one retained hold for each reason (`expired_permit_receipt` and `published_during_cancel`), drains current workers, and proves both holds and all associated liabilities remain unchanged. A missing, rewritten, or mismatched reason blocks the restart.
- Existing `0040` receipts and `0041` native publication schedule history remain byte/identity compatible.

## Migration and operational rollback

Migration is additive and does not backfill or fabricate holds for historical failures. Existing application readers continue to work because the new inspection member is additive. Narrow privileges allow the fixture worker to insert exact holds and authorized application readers to select them; no identity, grant, receipt, cycle, job, reservation, or publication mutation privilege is added.

Operational rollback first creates or identifies a qualified retained hold for each recovery reason, snapshots their exact receipt/effect/job/reservation/cycle/event/output/outbox liabilities, and stops/drains the compatible fixture publication worker. Before a prior-code worker may restart, the rollback verifier reads both committed hold rows directly, matches their immutable identities and reason-specific evidence, and proves every snapshotted liability is unchanged. It refuses restart if either reason is absent, rewritten, duplicated, or inconsistent. The prior code is then deployed while migration `0042` and its rows remain in place. Its existing `LegacyPublicationRecoveryHeld` behavior continues to fail closed, so rollback cannot replay or settle a held effect. Destructive downgrade is permitted only when the table is empty. Populated history requires a later separately reviewed migration; it is never deleted to make rollback pass.

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

This slice is complete only when the status correction, durable non-resumable hold, tracked rereview closure, active documentation reconciliation, focused/full/documentation verification, independent final review/meta-review, exact feature-commit push, and exact-commit CI all pass. It advances only the locally tested fixture portions of R18 and R32. It does not release production, RG0, RG1, R33, R34, P2–P7, optional P6, or `main` integration.
