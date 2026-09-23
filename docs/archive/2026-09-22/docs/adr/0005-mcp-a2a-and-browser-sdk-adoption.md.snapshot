# ADR 0005: Adopt official protocol SDKs at owned adapter boundaries

**Status:** Accepted on 2026-09-12

## Decision

Adopt `mcp==2.2.0` at a thin outbound adapter for MCP `2026-07-28`, retain negotiated support for the existing MCP `2025-11-25` fixture, and adopt `a2a-sdk==1.1.2` at a thin outbound adapter for A2A 1.0. A2A 0.3 is supported only through an explicit compatibility path and is marked deprecated. Make `playwright==1.62.0` an optional browser-research extra; normal installation does not install its browser binaries.

## Evidence and fit

The official MCP Python SDK 2.2.0 is MIT licensed and its v2 client discovers a current server before using legacy initialization. Its current protocol target is `2026-07-28`. The official A2A Python SDK 1.1.2 is Apache-2.0 licensed, implements A2A 1.0, and documents compatibility handling for 0.3 transports. Playwright 1.62.0 is Apache-2.0 licensed and provides isolated browser contexts plus trace and screenshot artifacts needed for governed, evidence-bearing research.

## Used surface and authentication

The MCP adapter uses discovery and read-only tool calls only. The A2A adapter uses Agent Cards, skills, task lifecycle, artifacts, and cancellation only. The browser adapter uses a fresh context, bounded navigation/extraction steps, response-size limits, and captures provenance artifacts. Each adapter receives an endpoint and an opaque `env://` secret reference from process configuration; secret values are resolved only by the existing scoped resolver and never stored in domain records, audit payloads, or traces. No protocol adapter may create write-capable external effects in Phases 5–6.

## Alternatives and trade-off

Continuing handwritten protocol clients would require owning rapidly changing wire compatibility, authentication, and cancellation behavior. Adding a generic agent platform is rejected because Salience owns its canonical contracts and policy boundary. Building a browser crawler is rejected because Playwright provides maintained browser protocol support. Browser automation is an optional, higher-cost fallback behind direct APIs and feeds, and remains disabled by default.

## Operations and failure behavior

Adapters validate configured protocol versions before remote calls, enforce request timeouts, and return owned DTOs with response provenance. Compatibility failures are explicit and include a migration direction; protocol/SDK classes never cross into canonical persistence. Browser setup is lazy because the current host does not have capacity for browser binaries. Operators must provision storage before installing the optional extra and must configure an allowlist before browser use.

## Exit path

`ToolGateway`, `RemoteAgentGateway`, source-connector contracts, and canonical records remain Salience-owned. Replacing an SDK changes only an edge adapter and its contract tests; it does not rewrite agent manifests, research evidence, signals, packages, briefs, or audit history.
