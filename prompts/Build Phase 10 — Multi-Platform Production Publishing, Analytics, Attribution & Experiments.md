# Part 1 — Finish real production publishing

Superseded by the [V4 production implementation blueprint](<../docs/v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-06:** Application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4` has completed Task 1–7 implementation and Task 8 local qualification: 116 focused tests passed, and the two-reason rollback rehearsal preserved both holds and liability snapshots through a local worker drain. The corrected-publication rereview returned `NO_FINDINGS_WITHIN_REVIEWED_FIXTURE_CORRECTION_SCOPE` for application `a4b2368cdb463939d093020224a5b5667392aee6`, with documentation and observed HEAD `d0311725686d81b7b4747c2d6225986e5ea3a524`; its meta-review accepted that result. That review predates Tasks 1–8 and does not review `7878208`. The clean template0-database full non-live suite on `7878208` passed **614 tests** with 0 failures, errors or skips and 3 Alembic deprecation warnings (917.37s pytest; 920.214s runner). It upgraded from zero relevant baseline rows to `0042_publication_recovery_holds`; the disposable database was dropped and the original database preserved. Local fixture evidence advances R18/R32 hold and ownership coverage only. Production effects remain disabled; production-wide revocation, general policy, routing and live schedule inventory, production recovery workers, R33/R34, RG0/RG1, and P2–P7 remain open or held. See [current recovery-hold evidence](../docs/v4/publication-recovery-hold-evidence.json).

[Original historical evidence](<../docs/archive/2026-09-22/prompts/Build Phase 10 — Multi-Platform Production Publishing, Analytics, Attribution & Experiments.md.snapshot.b64>) is retained with its inventory hash. This snapshot is base64-encoded to preserve its original missing final newline; decode using the archive instructions.

<!-- Historical heading anchors retained for incoming links. -->
<a id="release-truthfulness-gate"></a>
<a id="part-1--finish-real-production-publishing"></a>
<a id="publisherregistry"></a>
<a id="youtube-production-enablement"></a>
<a id="instagram-publisher-adapter"></a>
<a id="linkedin-publisher-adapter"></a>
<a id="future-publisher-adapters"></a>
<a id="provider-replacement-verifier"></a>
<a id="no-browser-publishing-bypass"></a>
<a id="part-2--canonical-analytics-system"></a>
<a id="canonical-analytics-entities"></a>
<a id="preserve-raw-metric-semantics"></a>
<a id="multi-horizon-metric-snapshots"></a>
<a id="youtube-analytics"></a>
<a id="instagram-analytics"></a>
<a id="linkedin-analytics"></a>
<a id="future-analytics-adapters"></a>
<a id="analytics-ingestion-idempotency"></a>
<a id="analytics-privacy-and-retention"></a>
<a id="part-3--deterministic-outcome-attribution"></a>
<a id="cost-and-business-outcome-attribution"></a>
<a id="conversion-events"></a>
<a id="part-4--experiment-system"></a>
<a id="experiment-contract"></a>
<a id="experiment-modes"></a>
<a id="experiment-assignment"></a>
<a id="statistical-engine"></a>
<a id="growth-analyst-agent"></a>
<a id="learningproposal-handoff"></a>
<a id="analytics-memory"></a>
<a id="observability"></a>
<a id="phase-10-deterministic-fixture-environment"></a>
<a id="phase-10-end-to-end-verifier"></a>
<a id="required-failure-tests"></a>
<a id="live-smoke-tests"></a>
<a id="documentation"></a>
<a id="definition-of-done"></a>
