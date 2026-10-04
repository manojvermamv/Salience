# Verification

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**V4 checkpoint — 2026-10-04:** P0 and P1 items 1–6 remain locally implemented. **Item 7 independent fixture qualification is COMPLETE:** all 17 P1 rows reviewed, findings repaired and independently re-reviewed on `e49a214`; **132 focused / 507 full non-live PASS**, zero failures/errors/skips, 316 source files and 250 mapped fixture cases. Use [current progress](v4/progress.md#current-state), [qualification evidence](v4/item7-evidence.json) and the [requirement matrix](v4/p1-qualification-matrix.json). RG0/full RG1 remain HELD, effects disabled, main unmerged and P2–P7 gated. Historical Phase 1–9 and earlier item evidence retain their dated scope.

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
