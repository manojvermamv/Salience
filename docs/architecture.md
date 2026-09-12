# Architecture

PostgreSQL is the canonical identity, governance, audit, provenance, budget,
agent, memory, evidence, and strategy store. Temporal is a replaceable durable
execution adapter behind owned workflow contracts; Garage is an S3-compatible
byte adapter behind the object-store contract.

The control API, CLI, and SDK use public schemas. Agents, model runtimes, MCP
tools, and A2A agents each cross a versioned project-owned contract before data
is persisted. Fixture implementations are deterministic and clean-deployment
safe; Phase 5 content production and publishing are intentionally excluded.
