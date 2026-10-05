# Local development

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 intelligence, selected briefs, native intelligence schedules, no-send dummy and dry creative integration are locally qualified (**116 focused / 585 full non-live PASS**, 344 source hashes) at `57eb84f`. Creative retains original scope/operation/outbox/permit, independently requires current stage authority, guards every activity and forbids provider effects. Run-pinned cancellation and earlier schedule identity/drain/rollback qualification are retained. See the [legacy integration contract](v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 goal remains active for publication/native publication schedules and retained obligations; production disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<archive/2026-09-22/docs/local-development.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

The opt-in item-6 wait driver requires `V4_FIXTURE_AUTOWAITS=1`, `SALIENCE_DEPLOYMENT_MODE=fixture`, disabled effects, a private local queue, the same workspace and explicit `V4_FIXTURE_WAIT_OPERATOR` with current `cycles:case_operator`. Retained pre-upgrade cases require explicit current-operator enrollment. Notification acceptance uses only the PostgreSQL no-effects fixture sink. Use the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract) and [verification commands](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution).

<!-- Historical heading anchors retained for incoming links. -->
<a id="local-development"></a>
