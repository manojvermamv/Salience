# Phases 1–4 Foundation Implementation Plan

Superseded by the [V4 production implementation blueprint](<../../v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 intelligence, selected briefs, native intelligence schedules, no-send dummy and dry creative integration are locally qualified (**116 focused / 585 full non-live PASS**, 344 source hashes) at `57eb84f`. Creative retains original scope/operation/outbox/permit, independently requires current stage authority, guards every activity and forbids provider effects. Run-pinned cancellation and earlier schedule identity/drain/rollback qualification are retained. See the [legacy integration contract](../../v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 goal remains active for publication/native publication schedules and retained obligations; production disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<../../archive/2026-09-22/docs/superpowers/plans/2026-09-12-phases-1-4-foundation.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Current execution status and handoff are maintained in [V4 progress](../../v4/progress.md#current-state) and the blueprint's [remaining P1 queue](../../v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). This historical Superpowers document is a navigation alias, not an active or newly completed V4 plan.

<!-- Historical heading anchors retained for incoming links. -->
<a id="phases-14-foundation-implementation-plan"></a>
<a id="global-constraints"></a>
<a id="task-1-establish-the-deployable-project-and-reuse-decisions"></a>
<a id="task-2-create-the-canonical-postgresql-model-and-migrations"></a>
<a id="task-3-implement-audit-provenance-tracing-secrets-permissions-policy-and-cost-controls"></a>
<a id="task-4-implement-storage-capability-registry-and-adapter-contracts"></a>
<a id="task-5-implement-phase-1-durable-jobs-effects-and-recovery"></a>
<a id="task-6-expose-the-phase-1-control-plane-and-checkpoint-its-verifier"></a>
<a id="task-7-add-canonical-callable-agent-entities-and-registry"></a>
<a id="task-8-implement-native-agent-execution-lead-agent-specialists-and-teams"></a>
<a id="task-9-add-agent-rest-cli-and-python-sdk-surfaces"></a>
<a id="task-10-implement-model-gateway-and-runtime-interchangeability"></a>
<a id="task-11-implement-mcp-tool-gateway-and-fixture"></a>
<a id="task-12-implement-a2a-remote-agent-gateway-and-fixture"></a>
<a id="task-13-add-scoped-memory-and-bootstrap-domain-records"></a>
<a id="task-14-implement-bounded-bootstrap-research-and-versioned-strategy-generation"></a>
<a id="task-15-complete-operations-documentation-and-the-cross-phase-verifier"></a>
<a id="plan-self-review"></a>
