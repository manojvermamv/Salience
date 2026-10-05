# Recovery and external effects

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Bounded parallel agent delegation is locally qualified (**45 focused / 540 full non-live PASS**, 325 source hashes) at `2653d2e`: one shared parent, scoped children, bounded concurrency/deadlines, durable claims/results and signed API/CLI/SDK commands. See the [parallel-agent contract](v4/parallel-agent-contract.md) for invocation, recovery and qualification. The completed 2026-10-04 item-7 independent review remains a dated qualification of its original source. Production stays disabled, RG0/RG1 HELD, main unmerged and P2–P7 gated.

[Original historical evidence](<archive/2026-09-22/docs/recovery.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Item 6 qualifies actual worker death, same-identity continuation/wait recovery, a disposable PostgreSQL snapshot restore and persisted Temporal server process restart. This preserves original intent/cycle/context/operation, liabilities and deduplicated fixture notification acceptance. Production coordinated DB/object/Temporal backup restore and measured RPO/RTO remain R66/P7 work. Rollback disables opt-in drivers, retains canonical sidecars and keeps compatible workers for patched histories; populated 0031 downgrade refuses destruction.

<!-- Historical heading anchors retained for incoming links. -->
<a id="recovery-and-external-effects"></a>
