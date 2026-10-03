# Recovery and external effects

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**V4 checkpoint — 2026-10-03:** P0 and P1 items 1–6 are locally qualified through item-6 `e5d1840` (85 focused / 456 full PASS). **Item 7 is IN PROGRESS:** local owner-binding hardening has **32 focused / 462 full non-live PASS**, with 311 source files; independent full P1 review remains **NOT RUN**. Use [current progress](v4/progress.md#current-state), [qualification evidence](v4/item7-preparation-evidence.json) and the [17-requirement matrix](v4/p1-qualification-matrix.json). RG0/full RG1 remain HELD, effects disabled, main unmerged and P2–P7 planned. Historical Phase 1–9 evidence below retains its original scope.

[Original historical evidence](<archive/2026-09-22/docs/recovery.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Item 6 qualifies actual worker death, same-identity continuation/wait recovery, a disposable PostgreSQL snapshot restore and persisted Temporal server process restart. This preserves original intent/cycle/context/operation, liabilities and deduplicated fixture notification acceptance. Production coordinated DB/object/Temporal backup restore and measured RPO/RTO remain R66/P7 work. Rollback disables opt-in drivers, retains canonical sidecars and keeps compatible workers for patched histories; populated 0031 downgrade refuses destruction.

<!-- Historical heading anchors retained for incoming links. -->
<a id="recovery-and-external-effects"></a>
