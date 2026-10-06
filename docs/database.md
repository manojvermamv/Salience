# Canonical PostgreSQL Data

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-06:** Application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4` has completed Task 1–7 implementation and Task 8 local qualification: 116 focused tests passed, and the two-reason rollback rehearsal preserved both holds and liability snapshots through a local worker drain. The corrected-publication rereview returned `NO_FINDINGS_WITHIN_REVIEWED_FIXTURE_CORRECTION_SCOPE` for application `a4b2368cdb463939d093020224a5b5667392aee6`, with documentation and observed HEAD `d0311725686d81b7b4747c2d6225986e5ea3a524`; its meta-review accepted that result. That review predates Tasks 1–8 and does not review `7878208`. The clean template0-database full non-live suite on `7878208` passed **614 tests** with 0 failures, errors or skips and 3 Alembic deprecation warnings (917.37s pytest; 920.214s runner). It upgraded from zero relevant baseline rows to `0042_publication_recovery_holds`; the disposable database was dropped and the original database preserved. Local fixture evidence advances R18/R32 hold and ownership coverage only. Production effects remain disabled; production-wide revocation, general policy, routing and live schedule inventory, production recovery workers, R33/R34, RG0/RG1, and P2–P7 remain open or held. See [current recovery-hold evidence](v4/publication-recovery-hold-evidence.json).

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
