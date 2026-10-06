# Part 1 — Complete the Phase 7–8 release gate

Superseded by the [V4 production implementation blueprint](<../docs/v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-06:** Application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4` has completed Task 1–7 implementation and Task 8 local qualification: 116 focused tests passed, and the two-reason rollback rehearsal preserved both holds and liability snapshots through a local worker drain. The corrected-publication rereview returned `NO_FINDINGS_WITHIN_REVIEWED_FIXTURE_CORRECTION_SCOPE` for application `a4b2368cdb463939d093020224a5b5667392aee6`, with documentation and observed HEAD `d0311725686d81b7b4747c2d6225986e5ea3a524`; its meta-review accepted that result. That review predates Tasks 1–8 and does not review `7878208`. The clean template0-database full non-live suite on `7878208` passed **614 tests** with 0 failures, errors or skips and 3 Alembic deprecation warnings (917.37s pytest; 920.214s runner). It upgraded from zero relevant baseline rows to `0042_publication_recovery_holds`; the disposable database was dropped and the original database preserved. Local fixture evidence advances R18/R32 hold and ownership coverage only. Production effects remain disabled; production-wide revocation, general policy, routing and live schedule inventory, production recovery workers, R33/R34, RG0/RG1, and P2–P7 remain open or held. See [current recovery-hold evidence](../docs/v4/publication-recovery-hold-evidence.json).

[Original historical evidence](<../docs/archive/2026-09-22/prompts/Complete Phase 7–8 Release Gate, Then Build Phase 9 Governed Publishing.md.snapshot.b64>) is retained with its inventory hash. This snapshot is base64-encoded to preserve its original missing final newline; decode using the archive instructions.

<!-- Historical heading anchors retained for incoming links. -->
<a id="owner-decisions--binding"></a>
<a id="part-1--complete-the-phase-78-release-gate"></a>
<a id="1-durable-creative-cost-lifecycle"></a>
<a id="2-complete-provider-lifecycle"></a>
<a id="3-registry-driven-capability-and-production-agent-execution"></a>
<a id="4-rights-and-provenance-enforcement"></a>
<a id="5-immutable-post-approval-distribution-decisions"></a>
<a id="phase-78-release-re-verification"></a>
<a id="part-2--phase-9-governed-publishing-end-to-end"></a>
<a id="canonical-phase-9-domain"></a>
<a id="account-and-credential-boundary"></a>
<a id="publisher-capability-and-certification-profile"></a>
<a id="publisher-adapter-contract"></a>
<a id="media-delivery-boundary"></a>
<a id="publication-authorization"></a>
<a id="durable-publication-cost-lifecycle"></a>
<a id="idempotent-external-publication-effect"></a>
<a id="scheduling"></a>
<a id="webhooks-and-remote-status"></a>
<a id="remote-receipt"></a>
<a id="synthetic-media-disclosure"></a>
<a id="publisher-policy-no-api-bypass"></a>
<a id="publication-agent"></a>
<a id="phase-9-fixture-verifier"></a>
<a id="phase-9-real-adapter-smoke-test"></a>
<a id="phase-9-end-to-end-verifier"></a>
<a id="minimal-post-publication-state--not-learning-yet"></a>
<a id="documentation-and-architecture-truthfulness"></a>
<a id="phase-9-definition-of-done"></a>
