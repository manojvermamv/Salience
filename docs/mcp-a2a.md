# MCP and A2A compatibility

The MCP fixture advertises protocol revision `2025-11-25` and rejects any other
revision before invocation. Its tool boundary validates scopes, input schema,
and timeout, then maps results to provider-neutral output/provenance records.

The official MCP Python SDK is MIT licensed and supports current standard
transports, but its stable v2 line is a breaking change. It is not adopted for
the fixture-only Phase 3 gateway: adding it would make a narrow contract test
own session, transport, and SDK lifecycle behavior prematurely. A later real
transport adapter must pin an SDK version, support its negotiated protocol
range, validate Streamable HTTP origin/authentication rules, and preserve this
contract.

The A2A fixture advertises protocol `0.3.0`, maps a descriptor, task result,
artifact, and parent run lineage, and rejects incompatible cards before an
invocation. The official Python SDK is Apache-2.0 and the protocol requires
explicit version negotiation; it remains deferred until a real transport needs
its capabilities. External Agent Cards, task states, and artifacts are treated
as untrusted input at that future adapter edge.
