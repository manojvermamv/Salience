# Protocol Compatibility

Compatibility is checked at the owned gateway boundary before an external request. Unsupported revisions fail closed with a migration message; protocol SDK objects and wire payloads are not canonical domain values.

| Protocol revision | SDK / adapter | Supported surface | Authentication boundary | Status |
| --- | --- | --- | --- | --- |
| MCP `2026-07-28` | `mcp==2.2.0` | discovery and read-only `tools/call` | configured bearer/OAuth material is resolved only at the adapter edge | supported |
| MCP `2025-11-25` | `mcp==2.2.0` negotiation path | legacy fixture discovery and read-only tools | no-auth fixture; production auth remains adapter-edge only | supported legacy |
| A2A 1.0 | `a2a-sdk==1.1.2` | Agent Card, skills, task, artifact, cancellation | configured auth material is resolved only at the adapter edge | supported |
| A2A 0.3 | `a2a-sdk==1.1.2` compatibility path | explicit legacy descriptor/task handling only | fixture-only until a configured adapter test is added | deprecated / migration required |

See `docs/adr/0005-mcp-a2a-and-browser-sdk-adoption.md` for the build-vs-adopt decision, fallback behavior, and replacement boundary.
