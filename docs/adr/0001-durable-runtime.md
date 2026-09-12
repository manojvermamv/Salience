# ADR 0001: Use Temporal behind an owned workflow boundary

**Status:** Accepted on 2026-09-12

## Decision

Use Temporal Server `1.23.1.1` (`temporalio/auto-setup`, pinned by digest in Compose) with Python SDK `temporalio==1.32.0`. The system exposes only its own `WorkflowBackend`, `Job`, `Checkpoint`, and `ExternalEffect` contracts.

## Evidence and fit

Temporal Server and its Python SDK are MIT-licensed. The SDK supports modern Python, including the project's Python 3.13 environment, and the official self-hosting guidance supports PostgreSQL-backed Docker deployments. The registry confirms `1.23.1.1` is the current published `auto-setup` server image tag; server and SDK release lines are independent, so compatibility is verified by the Phase-1 real-stack recovery test rather than assumed from matching version numbers.

## Alternatives and trade-off

A custom PostgreSQL queue/workflow runner would duplicate task dispatch, retries, timeout recovery, durable timers, schedules, cancellation, worker restart, and event history. n8n is not selected because its licensing does not fit a canonical embedded runtime. Temporal adds an operational service but eliminates the highest-risk custom distributed-systems code.

## Security and operations

The Temporal frontend stays on the internal Compose network; no UI service is deployed. Pin images, monitor upstream advisories, and upgrade before exposing a production control plane. PostgreSQL owns canonical job/checkpoint/effect/audit data so Temporal history can be rebuilt or migrated.

## Exit path

`WorkflowBackend` prevents Temporal types from crossing into domain/API records. A future backend reads the canonical job/checkpoint tables, migrates schedules, and replays only domain-safe work; Temporal workflow/run IDs remain external references.
