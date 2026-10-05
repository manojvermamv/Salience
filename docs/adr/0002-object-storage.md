# ADR 0002: Use Garage for the Phase-1 S3-compatible service

Superseded by the [V4 production implementation blueprint](<../v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Bounded parallel agent delegation is locally qualified (**45 focused / 540 full non-live PASS**, 325 source hashes) at `2653d2e`: one shared parent, scoped children, bounded concurrency/deadlines, durable claims/results and signed API/CLI/SDK commands. See the [parallel-agent contract](../v4/parallel-agent-contract.md) for invocation, recovery and qualification. The completed 2026-10-04 item-7 independent review remains a dated qualification of its original source. Production stays disabled, RG0/RG1 HELD, main unmerged and P2–P7 gated.

[Original historical evidence](<../archive/2026-09-22/docs/adr/0002-object-storage.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

<!-- Historical heading anchors retained for incoming links. -->
<a id="adr-0002-use-garage-for-the-phase-1-s3-compatible-service"></a>
<a id="decision"></a>
<a id="evidence-and-fit"></a>
<a id="rejected-candidate"></a>
<a id="security-and-operations"></a>
<a id="exit-path"></a>
