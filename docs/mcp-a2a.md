# MCP and A2A compatibility

Compatibility is checked at an owned gateway before an external protocol value
enters a Salience contract. `mcp==2.2.0` supports MCP `2026-07-28` discovery and
read-only tool calls, with an explicit `2025-11-25` legacy path. `a2a-sdk==1.1.2`
supports A2A 1.0 Agent Cards, skills, task artifacts, and cancellation, with an
explicit deprecated 0.3 compatibility path.

The adapters project only validated, JSON-compatible DTOs into canonical
records. Authentication material is resolved at the adapter edge; protocol SDK
objects, wire payloads, Agent Cards, and remote artifacts are not canonical
domain values. Unsupported revisions fail closed with a migration message.

The default loop does not require a remote MCP server or A2A agent. A configured
remote adapter must still observe secret scopes, trust classification, policy,
budget, audit, provenance, trace, and timeout boundaries. See
[`docs/compatibility.md`](compatibility.md) and
[`docs/adr/0005-mcp-a2a-and-browser-sdk-adoption.md`](adr/0005-mcp-a2a-and-browser-sdk-adoption.md).
