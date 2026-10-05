# Implementation Plan

Superseded by the [V4 production implementation blueprint](<../v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 intelligence, selected-brief and real scheduled-ingress integration is locally qualified (**92 focused / 561 full non-live PASS**, 335 source hashes) at `fcc5dfa`. Signed starts commit canonical admission/job/outbox; actual resumed Temporal ticks retain slot/operation identity, bounded authority and rollback fences. See the [legacy integration contract](../v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 integration goal remains active; production is disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<../archive/2026-09-22/docs/core/05_IMPLEMENTATION_PLAN.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Resume using [current V4 progress](../v4/progress.md#current-state) and the blueprint's [remaining P1 sequence](../v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). Completed local substrate and P1 slices must be reused; the archived checkboxes do not release V4 production gates.

<!-- Historical heading anchors retained for incoming links. -->
<a id="implementation-plan"></a>
<a id="phase-0--inspect-research-and-prove-assumptions"></a>
<a id="phase-1--deterministic-substrate-and-canonical-data"></a>
<a id="phase-2--canonical-agent-system"></a>
<a id="phase-3--agentframeworkprotocol-adapters"></a>
<a id="phase-4--niche-bootstrap-and-memory"></a>
<a id="phase-5--signals-research-and-strategy"></a>
<a id="phase-6--strategic-packaging-evidence-and-content-brief"></a>
<a id="phase-7--scriptcopy-and-creative-production"></a>
<a id="phase-8--distribution-packaging-and-governance"></a>
<a id="phase-9--publishing-analytics-and-learning"></a>
<a id="phase-10--browserapp-and-production-hardening"></a>
<a id="build-discipline"></a>
<a id="reusebuild-decision-rule"></a>
<a id="final-implementation-rule"></a>
