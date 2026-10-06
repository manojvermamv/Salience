# Control API

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-06:** Application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4` has completed Task 1–7 implementation and Task 8 local qualification: 116 focused tests passed, and the two-reason rollback rehearsal preserved both holds and liability snapshots through a local worker drain. The corrected-publication rereview returned `NO_FINDINGS_WITHIN_REVIEWED_FIXTURE_CORRECTION_SCOPE` for application `a4b2368cdb463939d093020224a5b5667392aee6`, with documentation and observed HEAD `d0311725686d81b7b4747c2d6225986e5ea3a524`; its meta-review accepted that result. That review predates Tasks 1–8 and does not review `7878208`. The clean template0-database full non-live suite on `7878208` passed **614 tests** with 0 failures, errors or skips and 3 Alembic deprecation warnings (917.37s pytest; 920.214s runner). It upgraded from zero relevant baseline rows to `0042_publication_recovery_holds`; the disposable database was dropped and the original database preserved. Local fixture evidence advances R18/R32 hold and ownership coverage only. Production effects remain disabled; production-wide revocation, general policy, routing and live schedule inventory, production recovery workers, R33/R34, RG0/RG1, and P2–P7 remain open or held. See [current recovery-hold evidence](v4/publication-recovery-hold-evidence.json).

[Original historical evidence](<archive/2026-09-22/docs/api.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Existing `CreativeProductionRequest@v1` and private publication contracts remain summarized in the blueprint evidence table. The explicit effects-disabled legacy fixture profile replaces the original publication routes with signed canonical requests, inspect/cancel, exact goal-revision account binding and paired native schedule adoption/binding. It requires current account/reconciliation grants and exact zero-budget fixture authorization; production subject/account/provider authorization remains unqualified. See the [legacy publication contract](v4/legacy-dispatch-contract.md#governed-publication-stage).

Signed local fixture commands retain API/CLI/SDK parity. Item 6 adds `GET /v1/v4/intents/{intent_id}`, SDK `cycles.inspect_intent` and CLI `cycles intent-inspect` under current `cycles:read`; revoked write authority never becomes execution permission. Case inspection exposes original owner, bounded waits and notification delivery/ack state; cycle inspection exposes its immutable runtime hold. See the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract).

<!-- Historical heading anchors retained for incoming links. -->
<a id="control-api"></a>
<a id="creative-production"></a>
<a id="governed-publication"></a>
