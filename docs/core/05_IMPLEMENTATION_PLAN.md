# Implementation Plan

Superseded by the [V4 production implementation blueprint](<../v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-06:** Application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4` has completed Task 1–7 implementation and Task 8 local qualification: 116 focused tests passed, and the two-reason rollback rehearsal preserved both holds and liability snapshots through a local worker drain. The corrected-publication rereview returned `NO_FINDINGS_WITHIN_REVIEWED_FIXTURE_CORRECTION_SCOPE` for application `a4b2368cdb463939d093020224a5b5667392aee6`, with documentation and observed HEAD `d0311725686d81b7b4747c2d6225986e5ea3a524`; its meta-review accepted that result. That review predates Tasks 1–8 and does not review `7878208`. The clean template0-database full non-live suite on `7878208` passed **614 tests** with 0 failures, errors or skips and 3 Alembic deprecation warnings (917.37s pytest; 920.214s runner). It upgraded from zero relevant baseline rows to `0042_publication_recovery_holds`; the disposable database was dropped and the original database preserved. Local fixture evidence advances R18/R32 hold and ownership coverage only. Production effects remain disabled; production-wide revocation, general policy, routing and live schedule inventory, production recovery workers, R33/R34, RG0/RG1, and P2–P7 remain open or held. See [current recovery-hold evidence](../v4/publication-recovery-hold-evidence.json).

[Original historical evidence](<../archive/2026-09-22/docs/core/05_IMPLEMENTATION_PLAN.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Resume using [current V4 progress](../v4/progress.md#current-state) and the blueprint's [remaining P1 sequence](../v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). Completed local substrate and P1 slices must be reused; the archived checkboxes do not release V4 production gates.

<!-- Historical heading anchors retained for incoming links. -->
<a id="implementation-plan"></a>
<a id="phase-0--inspect-research-and-prove-assumptions"></a>
<a id="phase-1--deterministic-substrate-and-canonical-data"></a>
<a id="phase-2--canonical-agent-system"></a>
<a id="phase-3--agentframeworkprotocol-adapters"></a>
<a id="phase-4--niche-bootstrap-and-memory"></a>
<a id="phase-5--signals-research-and-strategy"></a>
<a id="phase-6--strategic-packaging-evidence-and-content-brief"></a>
<a id="phase-7--scriptcopy-and-creative-production"></a>
<a id="phase-8--distribution-packaging-and-governance"></a>
<a id="phase-9--publishing-analytics-and-learning"></a>
<a id="phase-10--browserapp-and-production-hardening"></a>
<a id="build-discipline"></a>
<a id="reusebuild-decision-rule"></a>
<a id="final-implementation-rule"></a>
