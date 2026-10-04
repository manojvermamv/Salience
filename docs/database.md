# Canonical PostgreSQL Data

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**V4 checkpoint — 2026-10-04:** P0 and P1 items 1–6 remain locally implemented. **Item 7 independent fixture qualification is COMPLETE:** all 17 P1 rows reviewed, findings repaired and independently re-reviewed on `e49a214`; **132 focused / 507 full non-live PASS**, zero failures/errors/skips, 316 source files and 250 mapped fixture cases. Use [current progress](v4/progress.md#current-state), [qualification evidence](v4/item7-evidence.json) and the [requirement matrix](v4/p1-qualification-matrix.json). RG0/full RG1 remain HELD, effects disabled, main unmerged and P2–P7 gated. Historical Phase 1–9 and earlier item evidence retain their dated scope.

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
