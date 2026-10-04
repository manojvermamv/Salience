# Durable workflows

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**V4 checkpoint — 2026-10-04:** P0 and P1 items 1–6 remain locally implemented. **Item 7 independent fixture qualification is COMPLETE:** all 17 P1 rows reviewed, findings repaired and independently re-reviewed on `e49a214`; **132 focused / 507 full non-live PASS**, zero failures/errors/skips, 316 source files and 250 mapped fixture cases. Use [current progress](v4/progress.md#current-state), [qualification evidence](v4/item7-evidence.json) and the [requirement matrix](v4/p1-qualification-matrix.json). RG0/full RG1 remain HELD, effects disabled, main unmerged and P2–P7 gated. Historical Phase 1–9 and earlier item evidence retain their dated scope.

[Original historical evidence](<archive/2026-09-22/docs/workflows.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Item 6 qualifies SDK patch-based old/new history replay and actual worker transition, identity-preserving Continue-As-New, finite canonical timers and owner holds. Continuation preserves queued messages, the original absolute deadline and total message limit. Database/network work stays in bounded activities. Read the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract) for compatible-worker rollback and deployment routing obligations.

<!-- Historical heading anchors retained for incoming links. -->
<a id="durable-workflows"></a>
