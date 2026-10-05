# Durable workflows

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 intelligence, selected briefs, native intelligence schedules, no-send dummy and dry creative integration are locally qualified (**116 focused / 585 full non-live PASS**, 344 source hashes) at `57eb84f`. Creative retains original scope/operation/outbox/permit, independently requires current stage authority, guards every activity and forbids provider effects. Run-pinned cancellation and earlier schedule identity/drain/rollback qualification are retained. See the [legacy integration contract](v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 goal remains active for publication/native publication schedules and retained obligations; production disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<archive/2026-09-22/docs/workflows.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Item 6 qualifies SDK patch-based old/new history replay and actual worker transition, identity-preserving Continue-As-New, finite canonical timers and owner holds. Continuation preserves queued messages, the original absolute deadline and total message limit. Database/network work stays in bounded activities. Read the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract) for compatible-worker rollback and deployment routing obligations.

<!-- Historical heading anchors retained for incoming links. -->
<a id="durable-workflows"></a>
