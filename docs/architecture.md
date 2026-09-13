# Architecture

PostgreSQL is the canonical identity, governance, audit, provenance, budget,
agent, memory, source/fetch/evidence, signal, opportunity, strategy, package,
claim, immutable `ContentBrief@v1`, script, creative-production, asset,
distribution, disclosure, approval, and `ReadyToPublishPackage@v1` store.
Temporal remains the replaceable durable-execution adapter behind owned
workflow contracts; Garage is an S3-compatible byte adapter behind the
object-store contract.

The control API, CLI, and SDK use public schemas. `IntelligenceLoopWorkflow`
owns source collection through immutable brief assembly. `CreativeProductionWorkflow`
then starts only from that selected brief and checkpointingly creates scripts,
creative plans, storyboards, provider-job reconciliation state, assets, and
governed distribution packages. A versioned capability registry selects an
enabled compatible provider adapter for one to three bounded variants. Each
non-dry variant reserves canonical budget before submission, reconciles its
idempotent effect after restart, records a verified webhook receipt without raw
credentials, and settles actual cost before finalization. `CreativeService`
makes the final immutable ready-package handoff only after rights, profile,
metadata, claim-integrity, originality, synthetic-media disclosure, budget,
policy, and approval gates. Approved decision rows are insert-only: a changed
decision creates a new distribution and ready-package version.
MCP/A2A SDK values never enter canonical records. RSS/Atom and HN are read-only;
Playwright is optional and artifact-only. There is no live social publishing,
analytics, experiments, or learning loop.

The repository-grounded [Phase 1–8 Archify viewer](salience-phase-1-8-final.architecture.html)
is the interactive master diagram. It distinguishes canonical records from
replaceable object storage and fixture/credential-gated provider adapters, and
marks the ready package as a Phase-9 handoff rather than a publishing action.
Its checked source is [the Phase 1–8 architecture specification](salience-phase-1-8-final.architecture.json).
