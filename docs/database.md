# Canonical PostgreSQL Data

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Resumed P1 intelligence, selected briefs and native Temporal schedule conversion are locally qualified (**98 focused / 567 full non-live PASS**, 338 source hashes) at `10eb77f`. Native schedule UUIDs and row identity are preserved; authenticated source mapping, actual progress, physical drain, rollback fencing and original-slot admission/job/outbox are qualified. See the [legacy integration contract](v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 integration goal remains active; production is disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

[Original historical evidence](<archive/2026-09-22/docs/database.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

The current additive schema head is `0034_runtime_delivery_scope`. Additive 0032 preserves canonical hold ownership; 0033 adds immutable actor-bound goal-state commands and a separate monotonic state revision; 0034 rejects mismatched workspace/goal/intent/cycle and inbox bindings, preserves inconsistent retained history and records bounded terminal-runtime failure holds. Populated rollback refuses weaker protection; empty downgrade/re-upgrade is qualified. Existing waits, notifications, accounting and older authored migrations retain their identities and bytes. See [item 7 evidence](v4/item7-evidence.json); production data migration/coordinated restore remains gated.

Historical `0009_governed_publication` and hardening through 0012 remain preserved, including triggers that reject direct mutation of approved decisions.

<!-- Historical heading anchors retained for incoming links. -->
<a id="canonical-postgresql-data"></a>
<a id="migration"></a>
<a id="identity-and-isolation"></a>
<a id="creative-and-distribution-lineage"></a>
<a id="governed-publication-lineage"></a>
<a id="durable-runs"></a>
<a id="governance-and-traceability"></a>
<a id="extension-hooks"></a>
