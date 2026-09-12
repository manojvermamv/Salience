# Callable agents

Agent identity is an immutable `agent_id` plus semantic version. Manifests
declare schemas, scopes, effect classification, timeout, protocol compatibility,
trust classification, and delegated-authority hooks without naming a model,
framework, or provider runtime.

The canonical migration stores agent versions, runs, delegation edges, teams,
members, and events. Disabling a version retains its immutable history but
prevents new resolution. Runtime/model/provider selection belongs only in a run
mapping and provenance record, so adapters can be replaced without changing the
public agent identity.

The Phase 2 fixture executor validates JSON input/output schemas at one shared
boundary. Direct calls, delegated calls, and team members use that same service;
delegated calls receive a child trace context and retain their parent run ID.
The fixture runtimes are deterministic and do not invoke an AI/model provider.
