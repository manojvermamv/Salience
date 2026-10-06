# Reference Research

Superseded by the [V4 production implementation blueprint](<../v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-06:** Application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4` has completed Task 1–7 implementation and Task 8 local qualification: 116 focused tests passed, and the two-reason rollback rehearsal preserved both holds and liability snapshots through a local worker drain. The corrected-publication rereview returned `NO_FINDINGS_WITHIN_REVIEWED_FIXTURE_CORRECTION_SCOPE` for application `a4b2368cdb463939d093020224a5b5667392aee6`, with documentation and observed HEAD `d0311725686d81b7b4747c2d6225986e5ea3a524`; its meta-review accepted that result. That review predates Tasks 1–8 and does not review `7878208`. The clean template0-database full non-live suite on `7878208` passed **614 tests** with 0 failures, errors or skips and 3 Alembic deprecation warnings (917.37s pytest; 920.214s runner). It upgraded from zero relevant baseline rows to `0042_publication_recovery_holds`; the disposable database was dropped and the original database preserved. Local fixture evidence advances R18/R32 hold and ownership coverage only. Production effects remain disabled; production-wide revocation, general policy, routing and live schedule inventory, production recovery workers, R33/R34, RG0/RG1, and P2–P7 remain open or held. See [current recovery-hold evidence](../v4/publication-recovery-hold-evidence.json).

[Original historical evidence](<../archive/2026-09-22/docs/core/02_REFERENCE_RESEARCH.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

<!-- Historical heading anchors retained for incoming links. -->
<a id="reference-research"></a>
<a id="strong-production-oriented-building-blocks"></a>
<a id="n8n"></a>
<a id="moneyprinterturbo"></a>
<a id="postiz"></a>
<a id="litellm"></a>
<a id="agent-and-workflow-behavior-references"></a>
<a id="crewai--multi-agent-and-deterministic-flow-reference"></a>
<a id="openai-agents-sdk--supervisoragents-as-tools-reference"></a>
<a id="a2a-v10--remote-agent-interoperability"></a>
<a id="model-context-protocol--toolsresources-not-the-whole-agent-fabric"></a>
<a id="temporal--durable-execution-reference"></a>
<a id="specverifierenvironment-attribution-correction"></a>
<a id="langchain-social-media-agent"></a>
<a id="genesis-content-os"></a>
<a id="browser-and-engineering-agent-references"></a>
<a id="microsoft-playwright"></a>
<a id="browser-use"></a>
<a id="openhands"></a>
<a id="appium"></a>
<a id="verified-creative-generation-integrations-2026-09-12"></a>
<a id="higgsfield"></a>
<a id="heygen"></a>
<a id="synthesia"></a>
<a id="google-veo"></a>
<a id="architecture-conclusion-from-these-tools"></a>
<a id="reuse-first-component-selection-policy"></a>
<a id="research-derived-architecture-conclusions"></a>
<a id="research-rule-for-the-build-agent"></a>
