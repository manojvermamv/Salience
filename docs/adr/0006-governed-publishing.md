# ADR 0006: Governed publisher adapters and private YouTube sessions

Superseded by the [V4 production implementation blueprint](<../v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Bounded parallel agent delegation is locally qualified (**45 focused / 540 full non-live PASS**, 325 source hashes) at `2653d2e`: one shared parent, scoped children, bounded concurrency/deadlines, durable claims/results and signed API/CLI/SDK commands. See the [parallel-agent contract](../v4/parallel-agent-contract.md) for invocation, recovery and qualification. The completed 2026-10-04 item-7 independent review remains a dated qualification of its original source. Production stays disabled, RG0/RG1 HELD, main unmerged and P2–P7 gated.

[Original historical evidence](<../archive/2026-09-22/docs/adr/0006-governed-publishing.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

The historical decision selected an official YouTube Data API private-session boundary. Its original evidence is archived; complete production transfer/readback qualification is planned in V4 P2.

<!-- Historical heading anchors retained for incoming links. -->
<a id="adr-0006-governed-publisher-adapters-and-private-youtube-sessions"></a>
<a id="context"></a>
<a id="decision"></a>
<a id="consequences"></a>
