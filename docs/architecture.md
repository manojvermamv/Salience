# Architecture

PostgreSQL is the canonical identity, governance, audit, provenance, budget,
agent, memory, source/fetch/evidence, signal, opportunity, strategy, package,
claim, and immutable `ContentBrief@v1` store. Temporal is the replaceable
durable execution adapter behind owned workflow contracts; Garage is an
S3-compatible byte adapter behind the object-store contract.

The control API, CLI, and SDK use public schemas. The `IntelligenceLoopWorkflow`
keeps source collection, normalization, ranking, research/strategy delegation,
package selection, claim verification, and brief assembly under one canonical
job and trace. MCP/A2A SDK values never enter canonical records. RSS/Atom and
HN are read-only; Playwright is optional and artifact-only. Scripting, media,
publishing, analytics, experiments, and learning remain excluded.

The repository-grounded [Phase 1–6 Archify viewer](salience-phase-1-6.architecture.html)
is the interactive master diagram. Its source evidence is pinned to the commit
that introduced the implemented loop; it distinguishes running boundaries from
future provider adapters.
