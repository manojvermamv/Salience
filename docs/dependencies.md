# Dependency Inventory

This is the review record for the implemented Phase 1–8 branch. Exact Python
dependency pins are copied from `pyproject.toml`; image digests are copied from
`compose.yaml`.

| Component | Version selected | License | Boundary | Replacement path |
| --- | --- | --- | --- | --- |
| PostgreSQL | 17.9 | PostgreSQL License | canonical persistence | PostgreSQL-compatible migration/export |
| Temporal Server | 1.23.1.1 | MIT | `WorkflowBackend` | alternate durable backend + canonical job replay |
| Temporal Python SDK | 1.32.0 | MIT | `TemporalWorkflowBackend` | remove adapter |
| Garage | v2.3.0 | AGPL-3.0 | `S3ObjectStore` | alternate S3-compatible service |
| boto3 | 1.43.93 | Apache-2.0 | S3 transport only | any S3 client |
| FastAPI | 0.141.1 | MIT | HTTP transport | another ASGI/OpenAPI transport |
| SQLAlchemy | 2.0.52 | MIT | persistence repositories | SQL implementation behind repositories |
| Alembic | 1.20.0 | MIT | migrations | SQL migration scripts |
| asyncpg | 0.31.0 | Apache-2.0 | PostgreSQL transport | DBAPI behind repositories |
| psycopg[binary] | 3.3.5 | LGPL-3.0-only | Worker-side PostgreSQL boundary | Retain asyncpg for SQLAlchemy/migration paths; re-review LGPL terms before redistributing a bundled binary image |
| Pydantic | 2.12.5 | MIT | transport schemas | JSON-schema compatible types |
| Uvicorn | 0.40.0 | BSD-3-Clause | ASGI process | another ASGI server |
| JSON Schema | 4.26.0 | MIT | boundary validation | compliant validator |
| OpenTelemetry | 1.44.0 | Apache-2.0 | trace emission | compatible SDK/exporter |
| MCP Python SDK | 2.2.0 | MIT | owned MCP adapter edge | remove adapter; retain owned DTO contract |
| A2A Python SDK | 1.1.2 | Apache-2.0 | owned A2A adapter edge | remove adapter; retain owned DTO contract |
| Playwright | 1.62.0 (optional extra) | Apache-2.0 | read-only browser adapter | omit optional extra; retain browser contract |
| FFmpeg / ffprobe | host command; `NOT RUN` on 2026-09-13 | LGPL-2.1-or-later by default; optional GPL components alter obligations | `MediaEngine` command adapter | remove/replace the adapter; canonical assets remain portable |
| C2PA 2.4 / c2patool | optional; not installed | c2pa-rs MIT/Apache-2.0 | `C2paTool` provenance adapter | retain canonical provenance status; configure another conforming validator/signer |
| Synthesia REST | credential-gated; no live call | provider terms / paid plan | `SynthesiaCreativeProvider` owned HTTP DTO adapter | disable plugin; fixture providers and canonical provider-job history remain usable |

## Review cadence

Review image and package advisories before every release. Garage has an explicit
ADR; MCP and A2A use official SDKs only at owned, version-gated adapter edges.
Fixtures remain the deterministic default, and protocol compatibility is tested
rather than inferred. The Phase 7–8 build-vs-adopt decision retains
project-owned, versioned DTOs and reconciliation semantics while adopting only
optional, replaceable execution adapters. Before enabling media work, `MediaEngine` must pass its
free-space guard; it discovers but never installs `ffmpeg`/`ffprobe`. Current
host measurement is 4.1 GiB free with neither executable present, so real media
inspection/composition is `NOT RUN` until a guarded environment step installs
and verifies an appropriate distribution package. C2PA signing additionally
requires an operator-managed signer reference and is never inferred from an
asset's availability.
