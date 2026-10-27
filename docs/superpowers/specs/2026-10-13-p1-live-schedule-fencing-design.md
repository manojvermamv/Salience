# P1 Live Temporal Schedule Inventory and Fencing Design

> **Status:** COMPLETE_REVIEWED — bounded design reviewed; implementation and target qualification NOT RUN. Production remains disabled; RG0/RG1 HELD; P2 BLOCKED. This document does not authorize live schedule changes.
>
> **Audit source:** Commit `7c1ddcda280b510048ad61106c406171edd64f18`, branch `arch`, tree `af010fba37debad65b9494abc3b70db0a6a2f915`.
>
> **Audit environment:** Effects-disabled development host with local PostgreSQL and Temporal fixtures; environment manifest SHA-256 `18b15955b9a3be547bd1998ac2c1fce759c99d647fc3eeaa0ea21858e7a81436`. Production target and external acceptance are absent. The local application qualification is bound separately to `182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f`.
>
> **Decision source:** `docs/v4/p1-rg0-rg1-initial-gap-audit.json`, increment `live_temporal_schedule_fencing`, decision `REQUIRED`. Initial audit status is `COMPLETE_REVIEWED`; final-entry rows remain non-PASS and mapped production rows remain `HELD`.

## Intent

Close the audited production gap for the specific, owner-authorized Temporal schedules that feed current P1 cadence and delivery paths. The increment must enumerate the complete named schedule set in one deployment, bind each remote schedule to its canonical source, owner, goal revision, queue, and compatible worker floor, and make pause, cutover, stop, and rollback safe across remote timeouts and process restarts.

The fixture implementation already establishes useful behavior: a durable pending fence precedes activation; activation follows pause and readback; the canonical cursor gives scheduled work a stable slot; stop blocks polling; rollback records `rollback_pending` before the remote resume boundary; and rollback completes only after the old schedule's later slot is read back. Production work carries these state and safety semantics into an explicitly authorized environment. Fixture results remain fixture results.

The read-only audit traced those semantics through `src/salience/cycles/schedule_cutover.py` and the fixture-guarded `src/salience/cycles/temporal_schedule_control.py`. Relevant coverage includes `test_pending_fences_direct_poll_and_activation_reconciles_lost_pause_ack`, `test_cutover_poll_coalesces_concurrent_origins_and_crash_recovery`, `test_stop_holds_poll_and_legacy_resume`, and `test_unverified_external_unpause_cannot_be_mistaken_for_resume_ack` in `tests/integration/test_v4_schedule_cutover.py`; `test_actual_native_schedule_pause_rollback_tick_and_outbox` in `tests/integration/test_v4_native_schedules.py`; and `test_actual_native_publication_pair_is_adopted_fenced_and_resumed_through_outbox` in `tests/integration/test_v4_native_publication_schedules.py`. The audit records these as fixture/localhost evidence, with production acceptance held.

The initial audit found this increment required because the repository has no authorized production inventory or readback, while Temporal control is guarded to no-effects fixtures. The current host has no production Temporal target configuration, and no live schedule was inspected or changed. This design therefore supplies a future implementation and acceptance contract; it does not change any P1/RG0/RG1 row to `PASS`.

## Mapped requirements

| Row | Requirement owned by this increment | Initial owner role | Initial audit status | Contribution |
|---|---|---|---|---|
| R03 | Preserve atomic scheduled intent/outbox identity and acknowledged ordered delivery when schedule ticks cross into canonical V4 work. | Delivery runtime owner; named owner missing | `HELD` | Prove scheduled slots map to one canonical intent and stable operation identity. Provider acknowledgement and remote effect reconciliation remain with the recovery and external-acceptance increments. |
| R06 | Preserve a stable cycle identity and slot across manual, event, and scheduled requests and restarts. | Cadence identity owner; named owner missing | `HELD` | Prove the schedule contributes no duplicate canonical slot or cycle. |
| R08 | Enforce bounded overlap, catch-up, stale-slot, backfill, and independent-cadence rules for live schedules. | Schedule operations owner; named owner missing | `HELD` | Bind actual schedule behavior to the approved goal cadence and expose drift and cursor lag. |
| R18 | Apply emergency stop and policy outage fail-closed behavior to live schedule polling, pause, and restart. | Safety/reconciliation owner; named owner missing | `HELD` | Prove stop/revocation blocks new scheduled work and schedule reactivation until authorized readback. |

These are the primary rows assigned by the audit. This increment contributes schedule evidence only. It does not close RG0, complete RG1, or release Slice 7 or P2. Any unmapped or still-held row remains a blocker in the final exact-candidate/environment audit.

## Scope and exclusions

The target is the explicit set of currently deployed Temporal schedules named by an authorized inventory and linked to existing P1 goal, cadence, and delivery records. The scope includes inventory completeness and drift checks, schedule/source/queue/owner binding, pause and pause readback, in-flight action drain, first-slot selection, stable cursor operation, duplicate/coalescing behavior, stop and revocation response, monitoring, and rollback fencing.

The scope excludes new cadence or learning behavior, arbitrary cron/calendar/jitter support, new schedule creation, bulk conversion of every legacy schedule, dynamic worker routing, worker registration or artifact-floor policy, general production authorization policy, recovery-worker implementation, provider mutation or read-reconciliation policy, and external alert-channel delivery. It also excludes all P2 and later-phase capabilities. The routing, policy/revocation, recovery/alerts, and external acceptance specifications own those contracts and are required interfaces here.

## Design choice

Three approaches were considered:

1. Remove fixture guards and extend `TemporalFixtureLegacyScheduleControl` for production. This minimizes parallel code but combines fixture target restrictions and production credentials in one control path, weakening the isolation the existing tests enforce.
2. Keep a production schedule-control adapter separate from the fixture adapter and share the durable cutover, slot, and rollback state-machine contract. **Selected.** It preserves fixture-only enforcement while reusing the tested protocol and lets production authorization and target binding be explicit.
3. Use a manual runbook without durable controller state. This avoids service changes but cannot safely reconcile lost pause/resume acknowledgements, duplicate requests, or process restarts against the canonical cursor.

The selected implementation keeps production credentials and Temporal target configuration outside fixture code. The production adapter can only operate on schedule identities in the current authorized inventory. It does not create schedules or select a new cadence.

## Entry conditions, owners, and trust boundaries

Before any production qualification, record named primary and backup owners for schedule operations, the affected workflow and delivery paths, Temporal service ownership, and the on-call escalation destination. The initial audit has role names but no named human owners; that is an explicit hold. Each in-scope schedule also needs an accountable source/business owner and an authorized schedule operator. A missing owner, expired authorization, or unacknowledged escalation route holds the operation.

The implementation and qualification must consume these preceding contracts:

- **Policy and cross-service revocation:** current authorization, stop state, revocation ordering, and the narrow identity permitted to pause a schedule after action authority is revoked. Schedule controls do not mint or extend policy authority.
- **Production routing and worker fencing:** supported task queue, registered worker ownership, exact compatible artifact floor, in-flight drain evidence, and the route permitted for the old schedule during rollback. This specification does not define queues or worker rollout.
- **Production recovery and alerts:** unresolved operation and liability state, permitted read-only reconciliation, external alert delivery, acknowledgment, and escalation for holds that cannot be cleared. A schedule observation or alert acknowledgment does not resolve an operation.
- **External RG0/RG1 acceptance:** named target environment, operators, controls, and evidence acceptance. This design cannot supply external evidence from the audited development host.

Trust is separated across four systems. The inventory source establishes which schedules exist and who owns them. Temporal `Describe` and the authorized schedule-control API establish remote action, pause state, next execution, recent action history, and running actions. The Salience database establishes canonical goal/revision, stop, cursor, cutover state, idempotency, and durable operation identity. The routing and policy systems establish which worker and principal may act. A database mirror, client command response, or fixture configuration alone is not remote proof. A timeout means the remote result is unknown until a fresh readback matches the requested state.

The maximum age for inventory snapshots, remote readbacks, and outstanding-state alerts must be set in target-specific, versioned operations policy and reviewed by the schedule owner before qualification. Missing or expired limits make the operation held; implementations cannot silently choose a default.

Use separate least-privilege identities for read-only inventory/readback and for schedule pause/update. The schedule-control identity is constrained to the listed target namespace and schedule IDs and cannot start workflows or issue provider effects. The policy/revocation specification defines whether the narrow pause-only authority remains available after a workload grant is revoked. No credential, token, private payload, or secret value enters the inventory artifact, database, logs, or evidence package.

## Inventory rows and binding

The production inventory is a complete, paginated snapshot for a named Temporal target and namespace. Its manifest records the snapshot time, target alias, namespace, page/count completeness, source tool and version, candidate/configuration digests, and a deterministic SHA-256. Each schedule member has one row with these fields:

| Field | Required meaning |
|---|---|
| Target and remote identity | Registered target alias, Temporal namespace, remote schedule ID, and the manifest digest that enumerated it. |
| Canonical source | Local schedule/source record ID, source kind, workspace/tenant, goal ID, exact goal revision, and any paired publication schedule identity required by that source. |
| Ownership | Named accountable source owner, schedule operator, primary and backup on-call references, and the authorization evidence for inventory and mutation. |
| Action and route | Expected workflow type, action ID, argument digest, task queue, route ID, finite worker slot ID, owner boot ID, fencing epoch, artifact-lock digest, and the routing-spec worker floor that can drain and serve it. Store hashes and references, not raw secrets or private payloads. |
| Cadence and history | Supported interval/time-zone form, approved cadence revision, exact first V4 slot, observed last scheduled/started slot, action count or equivalent history watermark, history completeness, current remote state, next action, and running-action count. |
| Source integrity | Hash of the original action identity, local source identity, owner approval, and the readback snapshot. These identities are immutable after adoption. |

Inventory reconciliation compares the complete remote listing with the authorized manifest and the local source registry in both directions. An unknown remote schedule, a local source with no remote counterpart, duplicate binding, truncated page/history, unsupported cadence shape, wrong namespace, wrong action, owner/queue mismatch, or stale inventory blocks adoption and activation. Unknown schedules remain explicitly unclassified; the controller never adopts them by name pattern or by discovery alone. Out-of-scope schedules are listed with their owner and reason for exclusion. If an out-of-scope schedule can trigger the same canonical work, the operator must resolve that overlap before activation.

The initial target-specific inventory must identify each live native intelligence or governed-publication schedule in scope and bind any paired records. The cutover model supports one remote schedule per goal/revision, matching the existing one-goal cutover and source constraints; a paired publication record is part of that same schedule binding. Multiple goals may be qualified from the same complete inventory, but each cutover activates independently. If inventory finds two independently firing Temporal schedules for one goal, hold that goal and write a separate bounded design before adding multi-schedule group behavior. No schedule is activated from a partially verified inventory.

Only cadence forms represented by the current approved `CadencePolicy` and verified by the implementation are eligible. For the current interval-based contract, the schedule must have one supported interval, UTC whole-second aligned slots, and no undeclared calendar, cron, timezone, offset, or jitter behavior. First V4 slot must be an actual future Temporal action time, align with the goal cadence, precede the goal horizon end, and follow every observed legacy scheduled slot. If Temporal cannot prove a complete enough action-history boundary, the schedule stays held. Before activation, the routing specification must also read back the exact route ID, registered slot, owner/epoch, artifact-lock digest, and a compatible worker floor. `0043_complete_publication_recovery_liabilities` remains the fixture floor; a production route must run the exact qualified descendant containing the production policy/permit and recovery records, plus the versioned complete-inventory rollback verifier.

## State and operation contract

Retain the fixture state machine: `pending → active → rollback_pending → rolled_back`, with the existing permitted transition from `pending` to `rollback_pending` for an aborted preparation. `rollback_pending` has no transition back to `active`. Stop and revocation are orthogonal current policy states; clearing a stop never clears a cutover fence or resumes a remote schedule by itself.

| State/operation | Required behavior |
|---|---|
| Prepare (`pending`) | Persist immutable target, inventory, source, owner, revision, queue/action digest, first slot, and idempotency binding before issuing a remote pause. A repeated identical command returns the same record; any changed binding conflicts. No V4 scheduled admission is allowed. |
| Pause and readback | Pause the exact old schedule. After command success or timeout, independently describe its exact remote ID and verify paused state, expected action identity, current namespace/queue, and last slot before the first V4 slot. Reconcile this readback after restart. |
| Drain | Wait for all already-running old actions to reach the terminal/drained state required by the routing and recovery contracts. Do not terminate a production workflow as an implicit drain. A drain timeout or unknown action leaves the goal `pending` and the old schedule paused where confirmed. |
| Activate (`active`) | Recheck exact owner authorization, current policy/stop state, goal revision, source binding, remote readback, and drain. Initialize the canonical cursor to the predecessor of `first_v4_slot` and commit cursor plus activation state atomically. Activation is forbidden while the schedule is unknown or any stop is active. |
| Poll | Recheck active revision, current stop/revocation, and schedule-member fence before requesting due slots. Advance the cursor and immutable batch/intent receipts transactionally under the existing bounded catch-up/backfill and overlap rules. Stale or paused schedules do not cause guessed work. |
| Stop/revocation | Persist the canonical stop/revocation first so polling and new admission fail closed. Then issue pause to each authorized remote schedule and require describe readback. If the stop races activation, activation remains pending. If remote control is unavailable or its result is unknown, retain the local stop, keep the V4 fence, page the owner through the recovery/alerts contract, and report `HELD`. Clearing a stop requires fresh policy and remote readback before polling can continue. |
| Rollback start (`rollback_pending`) | Persist one rollback key and a fixed `rollback_after_slot` from the last committed V4 cursor, or the predecessor of the first V4 slot when no V4 slot was committed. This fence blocks all new V4 schedule polling and scheduled admission before any remote resume/update call. |
| Rollback completion (`rolled_back`) | Require a current compatible goal/revision, no active stop, no unresolved V4 or legacy dispatch, no unacknowledged outbox item, no open cycle or permit liability, and recovery-spec disposition of every unknown remote effect. Resume the authorized prior schedule/action through the supported route with start time strictly later than `rollback_after_slot`; read back the exact action/route, unpaused state, watermark, and next scheduled slot. Recheck all of these plus unresolved work before recording `rolled_back`. |

### Stable slot and coalescing rules

Use the canonical UTC slot and current goal revision as the stable scheduled identity, with the existing deterministic key `schedule:{goal_revision}:{slot.astimezone(UTC).isoformat()}` and immutable request/batch receipts. A scheduler retry, duplicate Temporal delivery, worker restart, or lost acknowledgement repeats the same identity. Manual, event, and scheduled origins for the same eligible slot coalesce to the same canonical intent/cycle under the existing cadence contract; they never create parallel cycles or outbox starts for that slot. Initialize the cursor to `first_v4_slot - cadence.interval` in the same transaction that changes the cutover to `active`.

The cursor is monotonic and durable. A scheduled poll can advance it only in the same transaction that records the selected slots and immutable batch. It uses the approved policy's bounded catch-up, overlap, stale, and backfill behavior. Reconciliation does not invent, backfill, or skip slots outside that policy. A revision change requires current cadence and authorization checks; a stale worker or schedule cannot advance the old revision. Schedule fencing guarantees one canonical slot identity. External effect exactly-once behavior remains the stable operation-ID and provider/reconciliation responsibility of the outbox and recovery specifications.

### Lost acknowledgements, concurrency, and restart

Every command uses a stable idempotency key bound to its complete request fingerprint. After an unknown Temporal response, the controller queries the exact target/namespace/remote ID and compares the readback with the requested state. It does not infer success from an HTTP/RPC timeout, local mirror, or scheduler process memory. An absent or conflicting readback leaves the state fenced and raises an owner-visible hold.

Concurrent prepare, activation, poll, stop, and rollback calls serialize on the canonical goal/revision state. The winner's immutable binding controls retries; a different owner, schedule, slot, route, or idempotency key conflicts. On process restart, the controller loads durable cutover/cursor state, re-enumerates or verifies the current inventory digest, describes every bound remote schedule, and reconciles outstanding operations before taking another action. It never resets the cursor or replays an uncertain remote mutation with a new identity.

Out-of-band schedule edits, unpause, deletion, or queue/action changes trigger a stop/hold and alert. The controller does not restore an observed prior configuration automatically because a human or other service may have changed it for a separate reason. Resolution requires an authorized new inventory and explicit owner action.

## Additive migration and history preservation

The existing `v4_schedule_cutovers`, `v4_schedule_cursors`, `v4_schedule_batches`, cycle request/event receipts, and native source/progress records already encode the fixture state-machine and immutable history. The native fixture source tables and `TemporalFixtureLegacyScheduleControl` are intentionally pinned to fixture targets and fixture queue names. Production target and namespace identity cannot be recorded by relaxing those fixture checks without changing their trust boundary.

Add one production-only, append-only schedule source-binding relation because the current fixture source records do not bind a production target alias/namespace and their queue constraints intentionally allow fixture queue names only. Each binding row must contain the registered target alias and namespace, remote schedule ID, source kind and local source IDs (including a paired publication ID where applicable), goal/workspace/revision, owner identity, task queue, workflow/action and argument digests, cadence, first V4 slot, observed legacy history watermark, and inventory evidence digest. Enforce one binding per goal under the current one-schedule-per-goal model and unique remote identity per target/namespace. Keep original binding fields immutable and reference the canonical goal/revision, source, and owner with foreign keys. Preserve current fixture tables and triggers unchanged. Reuse the existing immutable cycle-event stream for transition receipts when it can retain per-schedule readback hashes, timestamps, command IDs, and trace IDs; add no parallel observation ledger unless a focused schema test proves that stream cannot preserve required receipts.

Migration is additive and lock-bounded. Backfill is forbidden from fixture snapshots, naming conventions, environment variables, or incomplete Temporal history. Initial production bindings are created only from a complete authorized live inventory with two-sided local/remote source verification. Never overwrite original action history, source hashes, prior slots, or rollback watermarks. A downgrade refuses to delete any adopted production binding or control history; it preserves records and leaves the schedule fence in place for an operator-managed forward recovery.

## Observability and durable evidence

Expose per target and schedule: inventory capture time/digest/completeness; inventory-to-source drift count; last successful remote readback; remote pause/readback latency; running-action drain count and duration; current cutover/stop state and age; current and next canonical slot; cursor lag and skipped/coalesced slot counts; retry/lost-ack reconciliation count; unauthorized action/route drift; and rollback watermark/next-slot readback. Emit bounded structured events for prepare, each pause/readback, drain, activation, poll batch, stop, rollback start, rollback readback, completion, and every hold. Do not log secret values or raw workflow payloads.

Alert through the production recovery/alerts interface for unknown inventory members, stale or incomplete inventory, action/queue/owner drift, pause/readback failure, drain timeout, a stop that cannot pause all schedules, unexpected remote unpause, cursor lag beyond the approved bound, missing owner acknowledgment, and `pending` or `rollback_pending` beyond its approved response window. An alert is evidence that an operator was notified; it is not approval and it does not clear the hold.

The durable evidence package for each acceptance run contains:

- exact candidate commit, tree/artifact/config digests, migration revision, and environment manifest digest;
- authorized target alias and namespace, full schedule ID inventory, per-row source/owner/queue/action hashes, inventory completeness proof, and explicit out-of-scope rows;
- pre-change Temporal descriptions and history watermarks, command IDs, pause/drain readbacks, stable slot/cursor receipts, stop/revocation outcome, rollback watermark and later-slot readback where exercised;
- worker registration/artifact-floor and routing evidence from the routing increment, plus policy and recovery/alert evidence IDs;
- UTC timestamps, operator identities, trace IDs, test results, alert delivery/acknowledgement references, independent reviewer decision, and SHA-256 for every artifact.

Evidence must distinguish local fixture, CI, production, formal approval, and historical candidate scope. A current exact-head CI success does not prove live inventory or schedule state. Redact secrets before hashing or retaining logs. Any changed candidate, target, namespace, schedule set, source row, route, stale observation, or environment digest invalidates affected acceptance evidence and returns the row to `NOT RUN` or `HELD`.

## Cross-spec interfaces

| Owning specification | Required input or evidence from this increment | Contract owned by that specification |
|---|---|---|
| `2026-10-13-p1-production-policy-and-revocation-design.md` | Goal/revision, schedule binding IDs, current stop/revocation result, and the pause-only operator identity used for emergency fencing. | Current authorization, revocation ordering, permit issuance, pre-dispatch reauthorization, and in-flight effect liability. The schedule state machine cannot grant action authority. |
| `2026-10-13-p1-production-routing-and-worker-fencing-design.md` | Exact namespace/task queue, `route_id`, finite worker `slot_id`, `owner_id`, `fencing_epoch`, authenticated route-action decision, artifact-lock digest, and worker build compatible with the exact qualified production schema head based on `0043_complete_publication_recovery_liabilities`. | Route manifest, worker ownership/registration, artifact compatibility, linearized lease-action decisions, versioned rollback-launcher ABI, and the route for a prior schedule restart. This design owns no worker route or lease. |
| `2026-10-13-p1-production-recovery-and-alerts-design.md` | Schedule hold reason, exact source/operation identity, remote observation digest, rollback watermark, and owner/on-call reference. | Recovery-worker behavior, scoped remote reads, unknown-outcome reconciliation, external alert delivery/acknowledgment, retry bounds, and escalation. An alert never clears a schedule hold. |
| `2026-10-13-rg0-rg1-external-acceptance-design.md` | Complete target inventory, source/owner binding, pause/drain/cursor/stop/rollback readbacks, exact candidate and environment digests, and reviewer record. | External acceptance evidence and row disposition. This design never marks a row `PASS`. |

## Red-first acceptance tests

The implementation starts with failing deterministic control-contract tests and then qualifies the exact named Temporal deployment. Test names below are behavioral obligations; implementation may place them in the existing cutover/native-schedule suites or focused production suites.

1. `test_live_inventory_requires_complete_target_scoped_two_sided_binding`: missing pages, unknown remote IDs, missing local sources, absent named owners, duplicate bindings, multiple independently firing schedules for one goal, stale inventory, unsupported cadence, wrong namespace, wrong action digest, or wrong queue fail closed before activation or schedule mutation.
2. `test_production_binding_does_not_relax_fixture_target_guards`: production configuration cannot make a fixture adapter address a production target; fixture queue names and effects-disabled checks remain enforced in fixture tests.
3. `test_pending_requires_remote_pause_readback_and_drained_actions`: activation remains pending until the bound schedule is remotely paused, the latest old slot precedes the selected first V4 slot, action history is sufficiently complete, and all old running actions are drained under the routing contract.
4. `test_lost_pause_ack_or_source_readback_reconciles_one_exact_schedule`: a timeout after the pause never activates V4 until a fresh readback proves the exact schedule paused with matching source/action/route. Restart describes the same remote ID and repeats only the same idempotent request. An additional independent schedule for that goal remains held pending separate design.
5. `test_manual_event_and_scheduled_same_slot_coalesce_after_restart`: concurrent origins and duplicate schedule deliveries for one goal/revision/UTC slot return one canonical intent/cycle and stable outbox operation identity, including after worker/controller restart and lost acknowledgement.
6. `test_stop_or_revocation_racing_activation_and_poll_fails_closed`: a stop or policy outage at each operation boundary blocks activation/admission, preserves the current cursor, attempts authorized pause, and retains a visible hold until current policy plus remote readback permit continuation.
7. `test_out_of_band_unpause_or_action_route_drift_holds_and_alerts`: a remote schedule changed or unpaused after activation is detected by fresh readback, prevents new schedule admission, retains operation identity, and raises an owner-visible alert.
8. `test_crash_at_each_cutover_boundary_recovers_from_durable_state`: exercise crashes before/after pending commit, remote pause, drain, cursor/state activation commit, poll receipt, rollback-pending commit, remote resume, and final rollback receipt. Retries yield the same state and do not duplicate a slot or effect.
9. `test_unresolved_v4_work_or_remote_liability_blocks_rollback`: open cycle, unadmitted intent, unacknowledged outbox, live legacy dispatch, permit liability, or unknown remote outcome leaves rollback pending and the old schedule paused.
10. `test_rollback_requires_later_slot_readback_before_prior_route_restart`: after rollback, verify current old action/route binding, exact `rollback_after_slot`, unpaused state, and a future next slot strictly greater than that watermark. Missing or stale readback, mismatched worker floor, or a stop keeps prior-code restart blocked. A lost resume acknowledgement is resolved by describe and the same rollback key.
11. `test_candidate_and_environment_mismatch_never_passes_live_acceptance`: evidence from fixture scope, another application candidate, another target/namespace, incomplete inventory, or a stale manifest cannot satisfy a production row. With the audited host's absent target, the production acceptance result is `NOT RUN`/`HELD`.
12. `test_schedule_controller_has_no_effect_or_schedule_creation_authority`: the schedule-control principal can operate only on allowlisted schedule identities and cannot create a Temporal schedule, issue a provider effect, change cadence, or choose a new queue.

Live acceptance is run only after all entry dependencies and external authorizations are recorded for the exact target. It must include the complete inventory, pause/readback, one explicitly selected cutover, stop and rollback exercises that do not create unauthorized business effects, and an independently reviewed evidence package. If an exercise would start or mutate a real provider operation, it requires the separate production-effect authority and recovery procedure; this schedule design grants neither. No fixture test substitutes for the live acceptance run.

## Rollback verification and prior-code restart fence

Rollback is an explicit operation, not a best-effort restore. Before it changes a remote schedule, commit `rollback_pending` with a single immutable key and the current canonical cursor watermark. From that commit onward, all V4 schedule polling and new scheduled admission are fenced, even if Temporal is unavailable. Reconcile and settle every operation at or after the first V4 slot under the policy and recovery contracts. Preserve unknown outcomes and liabilities; do not discard them to make the cursor appear clear.

Before marking `rolled_back`, describe the bound schedule from the authorized target and verify the exact bound prior action or the routing-spec-approved compatible ingress action, owner/source identity, queue and compatible artifact floor, resume watermark, unpaused state, and next action strictly later than the saved cursor. Read the database binding and cursor again under lock, verify no new unresolved V4 work appeared, and persist the readback evidence with the terminal transition. If the Temporal update succeeded but its acknowledgment or database commit was lost, the state remains `rollback_pending`; the same key and fresh readback complete reconciliation.

Prior-code restart is separately blocked until the rollback verification record is complete on the exact candidate/environment, every applicable worker has the prior compatible artifact floor and registered route from the routing specification, current authorization and stop state permit that route, all schedule identities match, and the next old slot is later than the rollback watermark. A running prior-code process or Temporal acknowledgment alone is insufficient. A missing owner, unknown remote result, active stop, liability, or stale evidence keeps restart and P2 blocked.

## Completion boundary

This increment supplies only live schedule inventory, binding, cutover, stop, monitoring, slot-identity, and rollback-fence evidence for R03/R06/R08/R18. It does not prove external RG0/RG1 acceptance. The initial audited environment lacks a production target and named human owners, so live schedule acceptance is `NOT RUN` and the affected rows remain `HELD` until a later final audit binds all evidence to one unchanged candidate and environment.
