# Local development

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**V4 checkpoint — 2026-10-03:** P0 and P1 items 1–6 are locally qualified in no-effects fixtures at `e5d1840`: **85 focused / 456 full non-live PASS**, with both required CI checks and strict main protection PASS. Use [current progress](v4/progress.md#current-state), [completed work](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) and [validation](v4/validation.md#current-evidence-summary). Next is **item 7**, independent full RG1 qualification and outstanding production acceptance. RG0/full RG1 remain HELD, effects disabled, main unmerged and P2–P7 planned. Historical Phase 1–9 evidence below retains its original scope.

[Original historical evidence](<archive/2026-09-22/docs/local-development.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

The opt-in item-6 wait driver requires `V4_FIXTURE_AUTOWAITS=1`, `SALIENCE_DEPLOYMENT_MODE=fixture`, disabled effects, a private local queue, the same workspace and explicit `V4_FIXTURE_WAIT_OPERATOR` with current `cycles:case_operator`. Retained pre-upgrade cases require explicit current-operator enrollment. Notification acceptance uses only the PostgreSQL no-effects fixture sink. Use the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract) and [verification commands](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution).

<!-- Historical heading anchors retained for incoming links. -->
<a id="local-development"></a>
