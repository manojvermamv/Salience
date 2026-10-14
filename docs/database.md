# Canonical PostgreSQL Data



**Historical scope:** This superseded guide preserves earlier design, evidence, or navigation; its checkpoint is not current project status. Use [V4 progress](v4/progress.md#current-state) and [the V4 blueprint](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) for the active handoff.

[Original historical evidence](<archive/2026-09-22/docs/database.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

The current additive schema head is `0041_native_publication_sources`. M0040 adds immutable governed-publication legacy stage/account/receipt guards; M0041 stores the original paired native publication schedule source, independent progress and cutover plan. Both preserve original schedule/business identities and reject destructive downgrade after populated history. Earlier 0032–0039 scopes remain unchanged. Additive 0032 preserves canonical hold ownership; 0033 adds immutable actor-bound goal-state commands and a separate monotonic state revision; 0034 rejects mismatched workspace/goal/intent/cycle and inbox bindings, preserves inconsistent retained history and records bounded terminal-runtime failure holds. Populated rollback refuses weaker protection; empty downgrade/re-upgrade is qualified. Existing waits, notifications, accounting and older authored migrations retain their identities and bytes. See [item 7 evidence](v4/item7-evidence.json); production data migration/coordinated restore remains gated.

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
