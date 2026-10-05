# ADR 0005: Adopt official protocol SDKs at owned adapter boundaries

Superseded by the [V4 production implementation blueprint](<../v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 intelligence, selected-brief and real scheduled-ingress integration is locally qualified (**92 focused / 561 full non-live PASS**, 335 source hashes) at `fcc5dfa`. Signed starts commit canonical admission/job/outbox; actual resumed Temporal ticks retain slot/operation identity, bounded authority and rollback fences. See the [legacy integration contract](../v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 integration goal remains active; production is disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<../archive/2026-09-22/docs/adr/0005-mcp-a2a-and-browser-sdk-adoption.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

<!-- Historical heading anchors retained for incoming links. -->
<a id="adr-0005-adopt-official-protocol-sdks-at-owned-adapter-boundaries"></a>
<a id="decision"></a>
<a id="evidence-and-fit"></a>
<a id="used-surface-and-authentication"></a>
<a id="alternatives-and-trade-off"></a>
<a id="operations-and-failure-behavior"></a>
<a id="exit-path"></a>
