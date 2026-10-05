# Phase 9 Independent-Review Repairs Implementation Plan

Superseded by the [V4 production implementation blueprint](<../../v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 legacy intelligence integration is locally qualified (**87 focused / 556 full non-live PASS**, 332 source hashes) at `81bdeee`. Signed compatibility commands commit canonical admission/job/outbox; fixture execution retains the original operation/permit; disposable Temporal cutover and worker hard-exit recovery are tested. See the [legacy integration contract](../../v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 integration goal remains active; production is disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<../../archive/2026-09-22/docs/superpowers/plans/2026-09-13-phase-9-independent-review-repairs.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Current execution status and handoff are maintained in [V4 progress](../../v4/progress.md#current-state) and the blueprint's [remaining P1 queue](../../v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). This historical Superpowers document is a navigation alias, not an active or newly completed V4 plan.

<!-- Historical heading anchors retained for incoming links. -->
<a id="phase-9-independent-review-repairs-implementation-plan"></a>
<a id="global-constraints"></a>
<a id="task-1-add-canonical-approval-budget-and-immutability-schema-invariants"></a>
<a id="task-2-bind-request-and-schedule-execution-to-current-canonical-authority"></a>
<a id="task-3-hydrate-schedules-canonically-and-reauthorize-at-external-write-time"></a>
<a id="task-4-require-durable-youtube-edge-storage-and-close-the-release-gate"></a>
<a id="task-5-bind-approval-policy-rights-connection-and-capability-scope"></a>
<a id="task-6-materialize-canonical-jobs-for-scheduler-fired-executions"></a>
<a id="task-7-validate-normal-starts-before-creating-durable-jobs"></a>
<a id="task-8-re-certify-the-repaired-gate"></a>
<a id="plan-self-review"></a>
<a id="execution-handoff"></a>
