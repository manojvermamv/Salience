# Recovery and external effects

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 legacy intelligence integration is locally qualified (**87 focused / 556 full non-live PASS**, 332 source hashes) at `81bdeee`. Signed compatibility commands commit canonical admission/job/outbox; fixture execution retains the original operation/permit; disposable Temporal cutover and worker hard-exit recovery are tested. See the [legacy integration contract](v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 integration goal remains active; production is disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<archive/2026-09-22/docs/recovery.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Item 6 qualifies actual worker death, same-identity continuation/wait recovery, a disposable PostgreSQL snapshot restore and persisted Temporal server process restart. This preserves original intent/cycle/context/operation, liabilities and deduplicated fixture notification acceptance. Production coordinated DB/object/Temporal backup restore and measured RPO/RTO remain R66/P7 work. Rollback disables opt-in drivers, retains canonical sidecars and keeps compatible workers for patched histories; populated 0031 downgrade refuses destruction.

<!-- Historical heading anchors retained for incoming links. -->
<a id="recovery-and-external-effects"></a>
