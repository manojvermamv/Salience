# ADR 0004: Keep Phase-1 governance deterministic and protocol adapters optional

**Status:** Accepted on 2026-09-12

## Decision

Implement a small deterministic `PolicyEngine`, scoped environment `SecretResolver`, and control-plane token guard in core. Define extension contracts for OpenBao, OPA, OIDC, MCP, and A2A rather than deploying them all in Phase 1. Phase 3 adopts `mcp==2.2.0` and `a2a-sdk==1.1.2` only at adapter edges after their contract tests are written.

## Evidence and fit

OpenBao is an active MPL-2.0 self-hosted secret manager and OPA is an active Apache-2.0 general policy engine. They are substantial services for a narrow Phase-1 set of scope, effect-class, budget, approval, and dry-run checks. The MCP Python SDK has a maintained v2 line that negotiates current and older protocol revisions; the official A2A Python SDK is Apache-2.0 and has active 1.x releases with protocol-version validation.

## Alternatives and trade-off

Deploying OpenBao, OPA, and a full OAuth/OIDC provider now would increase credentials, bootstrap, backup, and operator complexity without improving the no-real-account Phase-1–4 verifier. Writing a general policy language or a secret vault is rejected; the custom component only evaluates owned product policy records and resolves external secret references.

## Security and operations

No secret value is persisted, returned by APIs, or emitted in audit/traces. The environment resolver accepts `env://` references only and checks required scopes. The control token is required at process start and applies an administrative scope ceiling. Protocol versions are stored as compatibility metadata; invalid ranges deny invocation before a remote effect.

## Exit path

OpenBao, OIDC, OPA, MCP, and A2A adapters conform to `SecretResolver`, `IdentityProvider`, `PolicyEngine`, `ToolGateway`, and `RemoteAgentGateway`. Replacing them does not rewrite canonical records.
