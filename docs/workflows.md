# Durable workflows



**Historical scope:** This superseded guide preserves earlier design, evidence, or navigation; its checkpoint is not current project status. Use [V4 progress](v4/progress.md#current-state) and [the V4 blueprint](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) for the active handoff.

[Original historical evidence](<archive/2026-09-22/docs/workflows.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Item 6 qualifies SDK patch-based old/new history replay and actual worker transition, identity-preserving Continue-As-New, finite canonical timers and owner holds. Continuation preserves queued messages, the original absolute deadline and total message limit. Database/network work stays in bounded activities. Read the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract) for compatible-worker rollback and deployment routing obligations.

<!-- Historical heading anchors retained for incoming links. -->
<a id="durable-workflows"></a>
