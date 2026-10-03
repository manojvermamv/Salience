# Canonical PostgreSQL Data

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**V4 checkpoint — 2026-10-03:** P0 and P1 items 1–6 are locally qualified in no-effects fixtures at `e5d1840`: **85 focused / 456 full non-live PASS**, with both required CI checks and strict main protection PASS. Use [current progress](v4/progress.md#current-state), [completed work](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) and [validation](v4/validation.md#current-evidence-summary). Next is **item 7**, independent full RG1 qualification and outstanding production acceptance. RG0/full RG1 remain HELD, effects disabled, main unmerged and P2–P7 planned. Historical Phase 1–9 evidence below retains its original scope.

[Original historical evidence](<archive/2026-09-22/docs/database.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

The current additive schema head is `0031_runtime_waits`: canonical event-triggered waits, immutable owner/runtime holds and bounded notification delivery/acceptance sidecars extend admission/context, accounting, governance, outbox and cutover migrations 0013–0030. Empty downgrade/re-upgrade and populated preservation refusal are qualified. Historical `0009_governed_publication` and hardening through 0012 remain preserved, including triggers that reject direct mutation of approved decisions. See the [item-6 runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract); production data migration/restore remains separately gated.

<!-- Historical heading anchors retained for incoming links. -->
<a id="canonical-postgresql-data"></a>
<a id="migration"></a>
<a id="identity-and-isolation"></a>
<a id="creative-and-distribution-lineage"></a>
<a id="governed-publication-lineage"></a>
<a id="durable-runs"></a>
<a id="governance-and-traceability"></a>
<a id="extension-hooks"></a>
