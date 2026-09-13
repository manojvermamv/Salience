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
`GovernedPublicationWorkflow` begins only after the immutable ready-package
handoff and a distinct current publication approval, and owns an immutable publisher request, idempotent plan/attempt,
remote-receipt reconciliation, status history, verified webhook receipt, cost
settlement, and terminal publication record. `PublisherRegistry` validates a
versioned capability profile and the worker requires a matching injected adapter;
account/connection identity and fresh policy/rights/approval/budget decisions are never
inherited from creative work. Scheduled executions receive only a canonical
publication-schedule ID from Temporal and reload their immutable request, plan, and budget.
The control API, CLI, and SDK expose scoped request, schedule, inspect, cancel,
and webhook routes without credential or package-mutation routes.

`YouTubePublisherAdapter` is a disabled-by-default official HTTP boundary. It
supports only private visibility and starts a resumable upload session with an
injected `youtube.upload` lease. The bearer value and opaque session URI remain
edge-only in a required durable injected session store; the safe session identity is the only
canonical projection and can be reconciled after adapter replacement. Generic
workflow submission fails closed until a future approved media-handoff adapter
is added. MCP/A2A SDK values never enter canonical records. RSS/Atom and HN are
read-only; Playwright is optional and artifact-only. There is no configured live
social post, analytics, experiments, or learning loop.

The repository-grounded [Phase 1–9 Archify viewer](salience-phase-1-9.architecture.html)
is the interactive master diagram. It distinguishes canonical records from
replaceable object storage and fixture/credential-gated publisher adapters, and
shows that publishing uses a new governed identity after the ready package.
Its checked source is [the Phase 1–9 architecture specification](salience-phase-1-9.architecture.json).
No secret value is represented in the canonical database schema.
