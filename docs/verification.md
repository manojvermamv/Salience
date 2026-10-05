# Verification

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 intelligence, selected-brief and real scheduled-ingress integration is locally qualified (**92 focused / 561 full non-live PASS**, 335 source hashes) at `fcc5dfa`. Signed starts commit canonical admission/job/outbox; actual resumed Temporal ticks retain slot/operation identity, bounded authority and rollback fences. See the [legacy integration contract](v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 integration goal remains active; production is disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<archive/2026-09-22/docs/verification.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Current [V4 validation](v4/validation.md#current-evidence-summary) records **132 focused / 507 full non-live PASS**, 2026-10-04, with 316 source hashes, three independent reviewers and all 17 P1 rows assessed. Independent fixture review and re-review pass at application `e49a214`; current formal PR approval is NOT RUN. Earlier item-6/preparation reports remain unchanged. Browser fixture tests use provisioned Playwright; live-browser/provider qualification remains NOT RUN. Every published head needs its own required CI/protection inspection.

Historical `bash scripts/verify-phases-7-8.sh` covers durable reservation/actual settlement and verified webhook deduplication. `bash scripts/verify-browser-evidence.sh` requires external provisioning and retains output under `artifacts/browser-evidence/`.

<!-- Historical heading anchors retained for incoming links. -->
<a id="verification"></a>
<a id="earlier-phase-16-baseline"></a>
<a id="browser-evidence"></a>
<a id="phase-78-creative-production"></a>
<a id="architecture-evidence"></a>
<a id="phase-9-governed-publishing"></a>
<a id="phase-19-architecture-evidence"></a>
