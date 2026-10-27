# P1 Production Policy and Cross-Service Revocation Design

**Design date:** 2026-10-27
**Status:** COMPLETE_REVIEWED — bounded design reviewed; implementation and target qualification NOT RUN. Production remains disabled; RG0/RG1 HELD; P2 BLOCKED. No implementation authority is granted by this document.
**Program slice:** 2, conditional bounded residual P1 policy and revocation work.

## Audit basis and decision

This design is based on the initial audit frozen at application/source commit `7c1ddcda280b510048ad61106c406171edd64f18`, tree `af010fba37debad65b9494abc3b70db0a6a2f915`, and effects-disabled development environment manifest digest `18b15955b9a3be547bd1998ac2c1fce759c99d647fc3eeaa0ea21858e7a81436` (`ip-172-31-13-145`). The local fixture qualification used application commit `182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f`; it is not production policy or deployment evidence. The audit records no production target, no production effects, no named production owner, and no external acceptance capability.

The audit decision is `REQUIRED` for `production_policy_and_cross_service_revocation`. The primary rows are P1/RG1 `R13`, `R15`, `R17`, and `R18`. Supporting rows are `R05`, `R07`, `R10`, and RG0 `R23F`. This slice contributes only to those row obligations. It does not close any RG0 row by itself; `R23F` remains held until the production policy and routing boundaries are accepted together. RG0, RG1, and P2 remain held until the final exact-candidate/environment audit records every applicable row as current `PASS`.

The uncovered work is production integration and evidence, not a replacement policy engine. Reuse the existing canonical policy versions, identity and scoped grants, current-authority snapshot, stop scopes and revisions, permit claim, typed approval binding, and publication authorization boundaries. A production policy record alone never enables effects.

## Intent, goals, and exclusions

The intent is to make every affected P1 service reject stale, missing, revoked, expired, or wrongly scoped authority at the point where it can authorize work. A revocation must reach every service that can issue or use a permit before that service can take its next action. Already claimed or remotely accepted work remains tied to its original identity and is tracked as an in-flight liability.

Goals:

- Load a versioned policy from the canonical `policy_versions` record and evaluate it alongside, not in place of, current subject identity, `permission_grants`, approved baseline, approval, budget, account, rights, and disclosure facts.
- Reauthorize transactionally before permit issuance and again at the dispatch claim boundary. Reauthorize before each later activity that can cause or advance an effect.
- Make grant changes advance the current subject authority revision so a revoke followed by a later grant cannot revive a previously frozen context or permit.
- Make an activated policy version terminal after revocation or supersession. A replacement uses a new policy-version identity and requires a new matching approval.
- Make stop and revocation checks consistent across the cycle/governance and publication paths. A cache or Temporal history is never positive authority.
- Preserve operation identity, immutable decisions, and all possible external liabilities through outage, crash, restart, and rollback.

Exclusions:

- Turning on production effects, credentials, spend, provider calls, or automatic acceptance.
- Worker routing, worker registration, deployment drain, artifact-floor changes, or worker rollback mechanics; these belong to the separate routing increment.
- Live Temporal schedule inventory, cutover, cancellation, or schedule rollback; these belong to the separate schedule increment.
- Recovery workers, external read reconciliation, alert delivery/acknowledgment, or terminal liability resolution; these belong to the separate recovery increment. This design defines the authority boundary those paths must honor.
- A new general policy microservice, policy mirror, policy language, provider-selection layer, adaptive routing, or P2 behavior.
- Treating LLM text, cached claims, a prior approval, a fixture decision, or a policy record by itself as authority.

## Approaches considered

1. **Read the current canonical authority at each boundary (selected).** Every service uses the current database primary and existing repositories/authorizers, with no positive authorization cache. The stop/permit transaction serializes against authority edits. This directly reuses the code and state model already tested in fixtures and gives revocation one immediate source of truth.
2. **Replicate policy into each service and rely on an invalidation bus.** Rejected as the authority path because a delayed or lost invalidation can make a stale replica authorize work. A notification may wake or cancel a worker, but it cannot replace the synchronous current-state check.
3. **Create a separate policy service and new policy database.** Rejected for this bounded increment because it duplicates `PolicyEngine`, `current_authority`, `CycleGovernance`, and `PublicationAuthorizer` without evidence that the production topology requires a second authority store.

If the production topology cannot give all affected services a strong read of one canonical authority source, production remains disabled until a separately reviewed linearizable read/propagation contract is supplied. An eventually consistent replica is not an acceptable substitute.

## Exact row scope and owners

| Row | This design owns | Acceptance boundary | Accountable owner role |
|---|---|---|---|
| `R13` | Current grant/policy revocation invalidates frozen authority and matching approvals. | A revoked or changed authority cannot issue or reuse a permit, even after worker restart. | Authority/revocation owner |
| `R15` | Versioned policy, exact approval purpose/effect/artifact/account/expiry binding, and no authority from model text. | Only a current policy plus a current operation-bound approval can authorize the requested effect. | Policy owner |
| `R17` | Policy/stop checks at the serialized permit issue and claim boundaries. | Concurrent stop/revoke versus claim has one durable order; no duplicate worker receives another send authority. | Effect dispatch owner |
| `R18` | Fail-closed authority outage and stop/revocation behavior; preserve the original in-flight liability. | No permit or forward action while authority is unavailable or revoked; uncertain work is held for its separate recovery contract. | Safety/reconciliation owner |
| `R05`, `R07`, `R10` | Carry current approved baseline, single admission decision, and frozen context into the authorization snapshot. | A permit cannot outlive or change its approved goal/context/baseline binding. | Goal/baseline governance, admission, and context owner roles |
| `R23F` | Retain default-deny route/effect policy. | A denied or unavailable route never substitutes another provider or policy. | Policy/release owner |

The audit has no named production owners. The role assignments above are design responsibilities, not invented human assignments. Before implementation or qualification, the release record must name the accountable human for each role and the production environment owner. Until then the relevant rows stay `HELD`/`NOT RUN`.

## Existing boundaries to preserve

- `policy_versions` is the canonical version identity and document/hash source. The existing `PolicyEngine` (`src/salience/governance/policy.py`) remains the decision model for allowed effect classes, approval-required effects, expiry, jurisdiction, scope, and budget. Production code must load and validate this model from canonical rows; an in-memory policy dictionary is only a unit-test fixture.
- `identity_subjects` and `permission_grants` remain the subject and scope authority. `current_authority()` (`src/salience/cycles/authority.py`) continues to resolve current workspace identity and exact grant, and `RunContext` remains a frozen snapshot rather than a standing permission.
- `CycleGovernance` (`src/salience/cycles/governance.py`) is the existing fixture stop/permit transaction boundary and remains fixture-only. Its serialization and current-authority semantics are the reference contract for a separately typed production effect path; do not claim that this fixture implementation is production-ready or weaken its fixture checks. `v4_stop_scopes` revisions, `v4_stop_commands`, `v4_permit_claims`, and immutable permit/event rows remain the canonical local stop and claim history. Stop-clear is a new revisioned command; it does not restore an old permit.
- `PublicationAuthorizer` (`src/salience/publication/governance.py`) continues to evaluate current publication request, native profile, account/connection, destination, locale, territory, visibility, approval, rights, disclosure, budget, and quota facts. The repository must derive policy status from canonical policy rows; a caller-supplied `policy_allowed` boolean is not production authority.
- Baseline approval, rights, disclosure, account scope, budget reservation, and provider capability are independent requirements. Policy approval cannot override any of them.
- The fixture permit literals (`fixture-account`, `fixture_execution`, `fixture.noop`) and fixture-only worker guard remain fixture-only. They must not be weakened or interpreted as a production permit. Any later production effect permit must have its own typed scope and effect identity and consume this same current-policy result.
- Production permits use the separately typed persistence path below. The existing fixture permit and claim relations, their literal checks, foreign keys, and current-pointer semantics remain byte-for-byte behaviorally unchanged; production rows are never inserted into or referenced by those fixture relations.

## Trust boundaries

1. **External identity to canonical subject.** The configured issuer validates issuer, audience, signature, expiry, and subject. `identity_subjects` maps that identity into exactly one workspace. Token claims identify a subject only; scope and effect grants come from the canonical database.
2. **Governance command to canonical state.** Only the named policy/identity governance roles may activate or revoke a policy or grant. Runtime roles may read current authority and append their allowed permit/audit records; they do not update policy, identity, grant, approval, or stop authority directly.
3. **Canonical database to service.** The primary database is the authority for identity revision, grant effect/scope/expiry, policy version/hash/status/expiry, approval decision, baseline, budget, and stop revision. Services use bounded, workspace-scoped queries and cannot accept worker payloads as substitutes for those rows.
4. **Temporal and worker boundary.** Workflow history, activity inputs, cached objects, and retries are untrusted snapshots. Every side-effecting activity reloads the original canonical operation and current authority. No positive decision survives a process restart without reauthorization.
5. **Effect adapter boundary.** A scoped, short-lived credential lease is resolved only after the final current authorization barrier. It is limited to the exact account, purpose, effect, and operation. It is never placed in workflow history, audit evidence, or a permit payload.
6. **External provider boundary.** A provider may accept an operation even if the response is lost. Revocation cannot undo that external fact; the operation remains an unresolved liability until its separate scoped reconciliation path proves a result or leaves a durable hold.

## State and operation identities

The authoritative operation tuple is `(workspace_id, goal_id, goal_revision, intent_id, cycle_id, context_id, operation_id, subject_id)`. It is linked to the exact program/baseline, policy version ID and `document_hash`, grant IDs and current subject revision, required scope set, approval request and immutable decision, effect class, purpose, artifact SHA-256, account/provider identity, budget reservation, and stop revisions. A production `approval_requests.request_context` for this path uses schema tag `salience.p1-effect-approval.v1` and contains those exact IDs, `effect_type`, purpose, artifact hash, account/provider ID, policy ID/hash, and timezone-aware `expires_at`; the row's workspace, policy foreign key, status, decider, and decision time must agree. Publication additionally binds `publication_request_id`, `job_id`, workflow/run ID, plan/attempt IDs, and any immutable remote receipt/effect ID.

Each authorization snapshot records the canonical references and a deterministic fingerprint of those references. It contains no token, secret, credential, or raw provider response. It is evidence about one decision and cannot be presented later as a bearer capability.

| Record | States and transition rule |
|---|---|
| Policy version | `draft -> active -> revoked` or `draft -> active -> superseded`. `active` expires when its explicit document expiry is reached. Once activated, its version identity, workspace, document, document hash, effective time, and jurisdiction set are immutable. A non-active version never becomes active again; publish a new version ID. |
| Scoped grant | Current allow can become deny/revoked or expire. Any decision-relevant edit advances the subject authority revision. A later grant is a new decision and must not restore a previously frozen context. Unknown scope or unsupported constraints deny. |
| Approval | `pending -> approved` or a terminal `rejected`/`revoked`/`expired` state. Approval identity binds the exact policy ID/hash, workspace/program/goal revision, context/operation, effect, purpose, artifact hash, account/provider, and expiry. A decided binding is never edited; changed terms require a new ID and decision. A revocation appends a separate immutable operation event while retaining the original approval decision fields. |
| Stop | Workspace and goal scopes use the existing monotonic stop revision. A stop wins against any issue/claim transaction ordered after it. Clearing requires a new authorized command and does not revive permits issued at an older stop revision. |
| Permit claim | `issued -> claimed -> unknown/resolved`; expired or stale `issued` permits cannot be claimed. `claimed` or `unknown` is never reset to `issued`. Only one current claim exists for the operation. |
| Authorization decision | `allowed` or `denied`, immutable and scoped to one exact operation, policy/grant/approval snapshot, effect, and time. A later decision is new evidence, not an edit to an earlier decision. |
| External operation | `not_submitted`, `submitting/unknown`, `accepted`, `completed`, or a verified terminal failure, represented through the owning workflow's canonical job/effect/receipt records. Any ambiguous provider result remains `unknown`; revocation does not relabel or delete it. |

Policy documents use a strict versioned schema projected into the existing `PolicyVersion` fields. `expires_at` is an explicit timezone-aware field in the canonical policy document and is compared with database time. A missing, malformed, future-effective, expired, revoked, wrong-workspace, or hash-mismatched version denies. `LLM` or generated text may provide a request proposal but cannot set policy, scope, approval, or permit fields.

## Authorization and revocation protocol

1. **Resolve the candidate operation.** Load the current workspace, goal revision, intent, context, program, approved baseline, actor identity, grant set, policy versions, approval, account/provider scope, budget, and stop rows. Verify every relation belongs to the same workspace and exact operation. Missing or duplicate canonical facts deny.
2. **Evaluate policy.** Load the referenced active policy row from the primary, validate the strict document schema and hash, compare its effect/scope/jurisdiction/expiry requirements, and evaluate the exact requested effect and budget. Preserve separate rights, disclosure, baseline, approval, account, capability, and budget denials. Unknown fields, scopes, or constraints deny.
3. **Issue one permit.** In one primary-database transaction, lock the subject/grant authority and policy rows against concurrent edits, lock the workspace and goal stop rows, verify the current approved context and baseline, synchronously obtain the route-fence decision defined below, and insert an immutable row into `v4_production_effect_permits`. Record current identity/grant revision, policy ID/hash, approval ID/expiry, effect, purpose, artifact, account, route-fence identity, and both stop revisions in the versioned canonical request snapshot. The permit lifetime is at most 60 seconds, and never exceeds the approval, policy, identity, grant, goal, route, or execution deadline.
4. **Claim at dispatch.** Before outbox dispatch or provider workflow registration, lock `v4_production_current_dispatch` for the exact operation and repeat the current authority, policy, approval, stop, budget, account, operation, and route-fence checks. Append one claim to `v4_production_effect_permit_claims` and advance the current pointer in the same transaction. A stop, revocation, route-owner transfer, expiry, or revision change ordered first denies the claim. A duplicate worker receives no second send authority.
5. **Reauthorize at every activity boundary.** Before each activity that can submit, deliver, publish, poll with state progression, cancel, or otherwise advance an external effect, reload the original operation and current canonical facts, obtain a fresh route-fence decision for that exact action, and compare current facts to the original effect binding. A revocation or route transfer between activities blocks the next action, even when the workflow payload is unchanged. Provider-specific cancellation and read reconciliation require their own current exact-scope authorization; a revoked permit does not imply permission to cancel or poll.
6. **Propagate revocation.** All affected services read the same current primary authority at these barriers and have no positive authorization cache. A revocation transaction becomes visible to the next barrier in every service. PostgreSQL notifications or workflow cancellation signals may accelerate stopping, but are advisory; a lost signal cannot authorize work. On startup, services must establish primary connectivity and current schema/artifact compatibility before registering or processing work. If the production topology cannot provide this read guarantee, it cannot enable the path under this design.
7. **Preserve in-flight liabilities.** Once a claim commits, record all subsequent state against the same `operation_id`, `permit_id`, subject, policy snapshot, account, and idempotency key. If the provider call may have started and the result is unknown, prohibit a second submit. Persist a durable hold with original identity and liability evidence; only a separate scoped read/recovery path may reconcile it. Never infer that revocation means “no effect happened,” release a reserved liability, switch identities/accounts/providers, or fabricate a terminal result.

The stop-versus-claim rule is intentionally ordered: if stop/revocation commits first, no claim or forward effect is allowed. If the single claim commits first, that exact claim may already be executing and is an in-flight liability; it does not authorize another effect or any later activity. Every subsequent barrier observes the newer stop/revocation revision and denies forward work. No design can retract a request already accepted by an external provider.

## Production permit persistence and route-fence decision

Production issuance is owned by the existing production effect-dispatch boundary under the policy owner. It does not use `CycleGovernance` or relax its fixture-only checks. The additive migration creates three production-only relations:

| Relation | Contract |
|---|---|
| `v4_production_effect_permits` | Immutable issued permit rows. Each row has a unique `permit_id`, exact `operation_id`, positive `generation`, workspace, policy ID/hash, subject/grant revision, approval ID, effect/purpose/account/provider binding, artifact digest, full route tuple, route-manifest and external-lock digests, issue/expiry times, and a request snapshot tagged `salience.p1-production-effect-permit.v1`. `UNIQUE(operation_id,generation)` prevents two permits for one generation. |
| `v4_production_effect_permit_claims` | Append-only claim rows referencing a production permit and repeating its operation/generation. `UNIQUE(permit_id)` and `UNIQUE(operation_id,generation)` allow one claim for a generation. A claim cannot reference `v4_dispatch_permits` or `v4_permit_claims`. |
| `v4_production_current_dispatch` | One mutable serialization pointer per operation: current generation, current permit ID, optional current claim ID, and the original idempotency key. A restricted database function advances the pointer only while holding the operation and authority locks. Its foreign keys target only the production relations. |

The production request discriminator rejects `fixture-account`, `fixture_execution`, and `fixture.noop`; the fixture request discriminator continues to reject production-only tags and identities. Cross-mode inserts, foreign-key references, claim lookups, and pointer updates fail closed in both directions. A production reissue may advance the generation only after the existing typed recovery command records authoritative no-effect evidence for the same operation and request fingerprint, current policy/stop checks pass, and the original provider idempotency key is retained. An `unknown`, accepted, or unresolved prior generation cannot be replaced. This permits a proven retry without minting a new business operation or hiding prior claims.

At permit issue, claim, and each later action, the production dispatch transaction calls the named platform's authoritative `RouteFenceAuthority` with the exact `(deployment_id, environment_id, route_id, slot_id, owner_id, fencing_epoch, route_manifest_sha256, artifact_lock_sha256, operation_id, permit_id, action_kind)` tuple. Its versioned `authorize_action` contract uses the lease service's clock and a linearizable per-slot order shared with claim, renewal, expiry, termination, and owner transfer; unknown, stale, expired, mismatched, or unavailable state denies. The authority returns a unique, one-use route-fence decision ID bound to that tuple. The production transaction persists/consumes that decision with the permit or action claim and the current policy/stop checks. A platform that only checks at startup, polls a cached epoch, or cannot order a decision against transfer does not satisfy this contract; production qualification remains `HELD` until a target demonstrates the required capability.

The linearization rule is explicit: if route transfer/expiry is ordered before `authorize_action`, the old process cannot obtain a decision. If a route-fence decision and matching production claim commit first, that one exact action is an in-flight liability and may have reached the provider; it never authorizes a second action. A stale process that resumes after TTL must obtain a new route-fence decision and current policy decision before every later action, and is denied. Existing pending/unknown claims are carried in the route drain and recovery inventories; transfer never erases or silently reassigns them.

The migration must include PostgreSQL acceptance for production-shaped permit issue, single claim, current-pointer advance, same-operation proven-no-effect generation advance, production and fixture preservation, and both cross-mode denial directions. It must preserve all existing fixture constraints and tests. Downgrade refuses populated production permit/claim/pointer history. Operational rollback disables new production permits, preserves the new relations and grants/revisions, and uses a versioned read-only rollback reader that can enumerate both fixture and production histories before any prior-code restart. An artifact that cannot read/preserve these records or enforce the same current route and policy barriers is not restart-compatible; 0043 remains the fixture floor, and production uses the exact additive schema head that contains this path plus the recovery evidence schema.

## Outage and restart behavior

- Failure to read the authoritative primary, policy document, current identity/grant, approval, stop state, or current operation denies new permits and dispatch claims. The system does not fall back to JWT scopes, workflow history, stale cache, replica reads, or an older policy version.
- If authority becomes unavailable after a permit is issued but before claim, the permit expires and cannot dispatch. If unavailable after claim but before the provider outcome is known, retain `unknown`/liability state and do not resubmit.
- If the worker crashes before claim, a restarted worker reloads the operation, current authority, and stop revisions and either claims a still-valid single permit or denies it. It cannot reuse a cached allow.
- If the worker crashes after claim or after possible provider acceptance, a restarted worker keeps the original operation identity. It may not create a replacement operation. It waits for the separate recovery path; absent exact read authority or provider evidence, the liability remains held.
- If a revocation signal is lost, the next synchronous authorization read still denies. Signal delivery failure is observable and alerted, but signal acknowledgment is not treated as authorization proof.
- A database failover or replica promotion must not serve an older authority revision as a positive decision. The database owner must prove commit durability and read consistency for stop, grant revision, policy state, approval, and permit claim before production qualification.

## Additive migration and preservation

An additive forward migration chain after 0043 is required because the inspected `v4_serialize_grant_edit()` trigger serializes identity/grant edits but does not advance `identity_subjects.revision`, and because production permit/claim history cannot fit the intentionally fixture-only relations. The current authority snapshot already carries that revision, so this repairs the existing boundary and adds only the production-specific facts needed by this design.

The migration must:

- Preserve every prior migration and row. Do not rewrite or backfill historical policy, grant, approval, context, permit, stop, or decision records.
- Add an `AFTER INSERT/UPDATE/DELETE` guard for identity `permission_grants` that advances the affected `identity_subjects.revision` in the same transaction whenever workspace, principal, scope, effect, constraints, or expiry changes. An update of a grant between `deny` and `allow` therefore cannot revive an old context or permit. Repeated same-value writes may conservatively advance the revision; they may never lower it.
- Add a policy-version transition guard: after a row has been active, its policy identity/document/hash/effective/jurisdiction fields cannot be rewritten, and a version that leaves `active` cannot return to `active`. Revocation or expiry history is not converted into a new active state; a new version uses a new ID.
- Add an approval-transition guard only for `approval_requests` tagged `salience.p1-effect-approval.v1`: require the exact binding fields above, forbid edits to those fields or original decision actor/time after decision, and forbid any terminal decision from returning to `pending` or `approved`. An approved decision may move once to `revoked`; the caller appends an immutable revocation event to the owning V4 cycle event chain in the same transaction. A replacement needs a new approval ID. Do not alter unrelated approval rows.
- Keep existing `v4_stop_scopes`, `v4_stop_commands`, `v4_permit_claims`, and immutable cycle events as the stop/claim record. Do not add a policy mirror or duplicate grant table.
- Add the production-only permit, claim, and current-dispatch relations under the exact contract above. Preserve fixture DDL, triggers, literals, permissions, and historical rows. Add a versioned read-only rollback reader for both mode-specific histories; do not downgrade a populated authority database or make a prior artifact read unknown production rows as safe to ignore.
- Fail closed on unknown legacy policy document shapes. No backfill may infer an expiry, scope, approval, or revocation from current state.
- Refuse downgrade after the migration has been applied to a populated authority database. Operational rollback disables new permits and preserves the new schema and revision values; it does not decrement identity revisions or restore old approvals. If safe emptiness cannot be proven, downgrade refuses.

If implementation discovers that the production authority store is not the shared primary described above, this migration is insufficient: stop and request a separate design for a durable, ordered revocation feed and consumer freshness evidence. Do not quietly add a cache or event bus to this slice.

## Required acceptance tests

All tests below must run first against disposable PostgreSQL/Temporal fixtures with effects disabled. Production qualification then repeats the applicable no-effects probes against one named environment, exact candidate, and named owners. Provider call counts and canonical rows are checked so denial is shown to happen before an external effect.

| Case | Setup | Required result |
|---|---|---|
| Stale authority snapshot | Issue a context/permit, change a decision-relevant grant, then attempt claim. | Grant mutation advances subject revision; stale context and permit deny. No adapter call, new operation, or rewritten context. |
| Missing authority | Omit subject, grant, policy, baseline, approval, account binding, or current stop row. | Deny before permit/dispatch; reason identifies missing canonical fact without exposing secrets. |
| Revoked grant | Revoke the exact required scope after context or permit creation. | New issue and claim deny. A later grant cannot revive the old context/permit because the authority revision changed. |
| Revoked policy | Revoke/supersede the active policy after approval or permit creation. | The old policy ID is terminal; claim and every later effect activity deny. New policy requires a new approval and permit. |
| Expired authority | Expire policy, grant, identity, approval, or permit at the boundary using database time. | Deny at the first check after expiry; no time-of-check cache or host-clock grace authorizes work. |
| Wrong scope/workspace | Substitute workspace, goal, program, scope, effect, purpose, account/provider, artifact hash, approval, context, operation, or policy ID/hash. | Deny before business/provider action. Assert zero provider calls and no cross-workspace reads/writes. |
| Stop wins concurrent claim | Race a workspace/goal stop command against permit issue/claim on independent connections. | If stop serializes first, no claim and no effect. The durable stop revision and denial event remain after retry/restart. |
| Claim wins concurrent stop | Race claim first, then stop before activity execution. | Exactly one committed claim may exist; every following activity sees the new stop and denies. Any possible provider outcome remains linked to the original operation. |
| Duplicate workers | Race multiple workers against the same operation/permit. | Exactly one claim can authorize dispatch; all duplicates are denied or return no-dispatch, with one operation and one immutable claim history. |
| Primary outage | Break authority database access before issue, before claim, between activities, and after claim. | No new permit/forward activity without current reads. After a possibly started provider call, persist or retain unknown liability; no retry/resubmit. |
| Crash before claim | Crash after issue and before claim; restart on a fresh process. | Re-read current policy/grants/stops. Current valid permit may claim once; stale, expired, revoked, or missing authority denies. |
| Crash after claim/acceptance | Crash immediately after claim and separately after simulated provider acceptance but before receipt persistence; restart. | No second submit. Preserve the original permit, operation, account, policy snapshot, reservation, and all existing liability records; transition to the recovery hold contract. |
| Revocation propagation | Revoke a grant/policy while cycle, publication, and another registered P1 service are independently paused between activities. | Each service's next primary read denies. Lost wake/signal does not permit work; a resumed/restarted service cannot use cached allow. |
| Production permit storage and mode separation | Issue and claim a production-shaped permit using the new production relations, then attempt fixture-to-production and production-to-fixture inserts, claims, and pointer references. | One production claim and current pointer are persisted with exact policy, approval, operation, account, artifact, and route bindings; all cross-mode attempts fail, and every existing fixture assertion remains unchanged. |
| Route fence at every action boundary | Pause a real worker past lease TTL; exercise route-service outage and owner termination/transfer before issue, claim, and each later action. | Only a current linearized `authorize_action` decision permits the boundary. A decision already committed is retained as one in-flight liability; no stale process obtains a later claim or provider call after resumption. If the target cannot prove this ordering, qualification is HELD. |
| Immutable decision evidence | Attempt to edit an activated policy, reuse its ID after revocation, or edit a decided approval into a different binding. | Database/service guard rejects mutation; create a new policy/approval identity for changed terms. Historical decisions remain readable. |
| No implicit provider substitution | Remove or revoke the requested provider/account/policy while another provider is configured. | Deny and hold; never choose the other provider/account or a broader scope. |

Existing local tests are reusable starting points, not sufficient acceptance by themselves: `tests/unit/test_policy.py`; `tests/integration/test_v4_cycle_governance.py` stop/permit ordering, concurrent claim, duplicate workers, subject revocation, authority outage, and immutable history cases; and `tests/integration/test_v4_legacy_publication.py` pre-start revocation, between-activity revocation, and lost reconciliation scope cases. Extend those assertions for policy IDs/hashes, revision advancement, production-shaped bindings, crash/restart persistence, and exact no-effect counts. Do not weaken the existing fixture assertions to accommodate production behavior.

## Rollback and prior-code restart fence

Rollback is a forward safety operation, not a schema downgrade. Before any prior candidate can restart:

1. Disable the production effect gate and deny new permit issuance. Stop affected workers and ingress using the separately owned routing/schedule procedures; this design does not authorize schedule or worker changes by itself.
2. Read back the exact candidate, environment manifest, schema revision, policy/approval/grant authority revisions, workspace/goal stop revisions, and worker registrations from the named environment.
3. Run the source-discriminated, complete-inventory rollback verifier through the routing-owned authenticated launcher ABI. It must cover fixture and production permits/claims plus every cycle, publication job, provider attempt/effect, budget reservation, receipt, recovery case/work item/hold, and current pointer in the target scope; no caller-supplied list may omit a row. Bind the deterministic inventory digest and pre/post fingerprints to the prior artifact and exact migration floor. Every claimed or possibly submitted operation must be terminally reconciled under its original identity or retained as a durable unresolved hold. Any `unknown`, missing receipt, unmatched reservation, active forward activity, or evidence mismatch blocks restart.
4. Verify no issued permit remains claimable; no stop or revoked policy/grant was cleared by rollback; no old approval or context was restored; and no operation identity, remote receipt, reservation, or audit record was deleted or rewritten.
5. Verify the prior artifact understands the exact production schema head and preserves the same fail-closed current-policy/revision/route checks. The authenticated external launcher must run the versioned read-only rollback reader before importing or executing prior application code. If the launcher, reader, or artifact cannot enumerate and preserve both fixture and production history or enforce the same barriers, do not restart it. Keep effects disabled and remain stopped.
6. Store a read-only verifier result bound to the prior artifact digest, candidate being rolled back, exact environment manifest, schema head, operation-liability inventory digest, and named operator. A reviewer independently checks it before the prior process starts.

The existing fixture verifier and `182c4e4` local result cannot satisfy this production rollback fence. Rollback never clears an unresolved liability and never converts an old policy/approval into current authority.

## Observability and durable evidence

Emit structured allow/deny decisions for policy load, permit issue, claim, each activity reauthorization, stop change, and authority-read failure. Each record binds the operation tuple, policy ID/hash, subject revision, grant IDs/scopes, approval ID/expiry, effect/purpose, artifact hash, account/provider identity, stop revisions, permit/claim IDs, UTC database decision time, trace ID, code/artifact digest, and reason codes. Redact credentials, tokens, secret values, full provider payloads, and sensitive request text. Do not use identifiers as metric labels.

Persist decision events through the existing immutable cycle events and the owning publication/job audit path in the same transaction as the permit/claim or before any effect. The in-memory `InMemoryGovernanceJournal` is test-only and cannot be the production record. Production database roles must prevent runtime workers from editing or deleting policy, grant, approval, stop, permit, and decision history outside their authorized command path.

Required metrics and alerts include authorization allows/denials by bounded reason/effect, stale revision denials, policy/grant/approval expiry, stop-to-next-denial latency, authority database outage, revocation signal lag/failure, duplicate claim attempts, unknown external outcomes, and unresolved liabilities. The security/identity and operations owners set and record a measurable revocation-latency objective before qualification. Regardless of that objective, the synchronous barrier must deny on the first request ordered after the committed revocation; notification latency cannot weaken that rule.

Each evidence record includes exact source/candidate commit, full artifact/image digest, migration head, exact named environment and secret-free manifest digest, issuer/audience identifiers (no keys or tokens), database consistency/failover evidence, policy IDs and hashes, redacted grant/approval/stop/permit references, test/probe command and result, timestamps, operator/reviewer identities, and immutable log/evidence digests. Evidence scopes remain distinct: fixture, production no-effects acceptance, production effect acceptance, CI, formal approval, and historical candidate. A fixture `PASS`, earlier commit, or different environment never transfers. This design cannot grant production authority or change P2 gate status.

## Open prerequisites before implementation

- Name the human owners for the policy, authority/revocation, effect dispatch, safety/reconciliation, admission, baseline, context, and production environment roles.
- Identify the production issuer, audience, primary database, service connectivity/role grants, policy provisioning and revocation operator, and required read consistency/failover guarantees.
- Confirm that policy documents can use the strict expiry/effect/scope schema and that the approval route can preserve the exact immutable operation binding.
- Record the production revocation-latency objective and the allowed exact production operation/effect scope. Until all are supplied, this remains a design draft and production remains disabled.

## Source references

- `docs/superpowers/plans/2026-10-27-p1-rg0-rg1-initial-gap-audit.md`, Tasks 3–6.
- `docs/v4/p1-rg0-rg1-initial-gap-audit.json`, decision `production_policy_and_cross_service_revocation`, rows `R13`, `R15`, `R17`, `R18`, `R05`, `R07`, `R10`, and `R23F`.
- `docs/v4/p1-qualification-matrix.json`, acceptance and remaining-acceptance records for the rows above.
- `docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md`, Slice 1 and Slice 2.
- [durable policy/routing audit](../../v4/audits/salience-p1-policy-routing-audit.md), policy/revocation findings and local evidence boundaries.
- `src/salience/governance/policy.py`, `src/salience/cycles/authority.py`, `src/salience/cycles/governance.py`, `src/salience/publication/governance.py`, `src/salience/publication/repository.py`, and `src/salience/cycles/legacy_publication_runtime.py`.
- `migrations/versions/0013_identity_boundary.py`, `migrations/versions/0021_cycle_policy.py`, and `migrations/versions/0026_cycle_governance.py`.
- `tests/unit/test_policy.py`, `tests/integration/test_v4_cycle_governance.py`, and `tests/integration/test_v4_legacy_publication.py`.
