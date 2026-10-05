# Dependency Inventory

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Bounded parallel agent delegation is locally qualified (**45 focused / 540 full non-live PASS**, 325 source hashes) at `2653d2e`: one shared parent, scoped children, bounded concurrency/deadlines, durable claims/results and signed API/CLI/SDK commands. See the [parallel-agent contract](v4/parallel-agent-contract.md) for invocation, recovery and qualification. The completed 2026-10-04 item-7 independent review remains a dated qualification of its original source. Production stays disabled, RG0/RG1 HELD, main unmerged and P2–P7 gated.

[Original historical evidence](<archive/2026-09-22/docs/dependencies.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Live provider capabilities are disabled by default. Current pins remain in `pyproject.toml`; fresh production license/advisory review is required by P0.

<!-- Historical heading anchors retained for incoming links. -->
<a id="dependency-inventory"></a>
<a id="review-cadence"></a>
<a id="governed-publishing-decision"></a>
