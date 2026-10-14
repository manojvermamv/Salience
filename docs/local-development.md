# Local development



**Historical scope:** This superseded guide preserves earlier design, evidence, or navigation; its checkpoint is not current project status. Use [V4 progress](v4/progress.md#current-state) and [the V4 blueprint](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) for the active handoff.

[Original historical evidence](<archive/2026-09-22/docs/local-development.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

The opt-in item-6 wait driver requires `V4_FIXTURE_AUTOWAITS=1`, `SALIENCE_DEPLOYMENT_MODE=fixture`, disabled effects, a private local queue, the same workspace and explicit `V4_FIXTURE_WAIT_OPERATOR` with current `cycles:case_operator`. Retained pre-upgrade cases require explicit current-operator enrollment. Notification acceptance uses only the PostgreSQL no-effects fixture sink. Use the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract) and [verification commands](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution).

<!-- Historical heading anchors retained for incoming links. -->
<a id="local-development"></a>
