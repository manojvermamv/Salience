# Phase 1 — Deterministic substrate and canonical data

Superseded by the [V4 production implementation blueprint](<../docs/v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-06:** Application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4` has completed Task 1–7 implementation and Task 8 local qualification: 116 focused tests passed, and the two-reason rollback rehearsal preserved both holds and liability snapshots through a local worker drain. The corrected-publication rereview returned `NO_FINDINGS_WITHIN_REVIEWED_FIXTURE_CORRECTION_SCOPE` for application `a4b2368cdb463939d093020224a5b5667392aee6`, with documentation and observed HEAD `d0311725686d81b7b4747c2d6225986e5ea3a524`; its meta-review accepted that result. That review predates Tasks 1–8 and does not review `7878208`. The clean template0-database full non-live suite on `7878208` passed **614 tests** with 0 failures, errors or skips and 3 Alembic deprecation warnings (917.37s pytest; 920.214s runner). It upgraded from zero relevant baseline rows to `0042_publication_recovery_holds`; the disposable database was dropped and the original database preserved. Local fixture evidence advances R18/R32 hold and ownership coverage only. Production effects remain disabled; production-wide revocation, general policy, routing and live schedule inventory, production recovery workers, R33/R34, RG0/RG1, and P2–P7 remain open or held. See [current recovery-hold evidence](../docs/v4/publication-recovery-hold-evidence.json).

[Original historical evidence](<../docs/archive/2026-09-22/prompts/Build Phases 1–4 End-to-End.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

<!-- Historical heading anchors retained for incoming links. -->
<a id="current-environment-and-proposed-foundation"></a>
<a id="scope-boundary"></a>
<a id="phase-1--deterministic-substrate-and-canonical-data"></a>
<a id="phase-1-verifier"></a>
<a id="phase-2--canonical-callable-agent-system"></a>
<a id="phase-2-verifier"></a>
<a id="phase-3--model-framework-tool-and-remote-agent-adapters"></a>
<a id="model-gateway"></a>
<a id="agent-framework-adapters"></a>
<a id="mcp"></a>
<a id="a2a"></a>
<a id="phase-3-verifier"></a>
<a id="phase-4--niche-bootstrap-and-scoped-memory"></a>
<a id="bootstrap-research"></a>
<a id="memory"></a>
<a id="strategy-output"></a>
<a id="phase-4-verifier"></a>
<a id="cross-phase-invariants"></a>
<a id="core-owns-identity"></a>
<a id="durable-execution"></a>
<a id="typed-boundaries"></a>
<a id="agent-identity-is-independent"></a>
<a id="least-privilege"></a>
<a id="dry-run-first"></a>
<a id="provenance"></a>
<a id="cost-control"></a>
<a id="future-production-hooks"></a>
<a id="reuse-first"></a>
<a id="required-project-documentation"></a>
<a id="final-phase-14-end-to-end-verification"></a>
<a id="completion-criteria"></a>
