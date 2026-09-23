# ADR 0003: Use Python contract-focused service libraries

**Status:** Accepted on 2026-09-12

## Decision

Use Python 3.13 with FastAPI `0.141.1`, SQLAlchemy `2.0.52`, Alembic `1.20.0`, asyncpg, Psycopg `3.3.5`, JSON Schema `4.26.0`, and OpenTelemetry API/SDK `1.44.0`. Pin exact resolved package versions in `pyproject.toml` and this inventory after installation.

## Evidence and fit

FastAPI, SQLAlchemy, Alembic, and JSON Schema are MIT-licensed; OpenTelemetry and asyncpg are Apache-2.0; Psycopg is LGPL-3.0-only. SQLAlchemy, JSON Schema, and Psycopg explicitly support Python 3.13. These are maintained libraries with public releases and security-reporting processes. FastAPI supplies an OpenAPI control surface; SQLAlchemy/Alembic provide PostgreSQL migrations; JSON Schema validates plugin/agent/tool boundaries; OpenTelemetry supplies standard trace propagation without owning audit data.

Psycopg's binary extra is confined to the worker-side PostgreSQL repository. It replaces asyncpg only for Temporal activity I/O after asyncpg reproducibly caused native Python 3.13 segmentation faults in that runtime; asyncpg remains the SQLAlchemy/Alembic transport. The LGPL license is acceptable for this dynamically linked application dependency but must be reviewed again before redistributing a bundled binary image.

## Alternatives and trade-off

Hand-written migration, HTTP, schema, and tracing frameworks would increase maintenance without differentiating the product. The project does not adopt a provider/model framework, full OAuth server, or observability vendor. Those remain adapters around core-owned contracts.

## Exit path

FastAPI is confined to transport routes; SQLAlchemy and driver choices are confined to repositories/migrations; OpenTelemetry spans mirror canonical trace IDs and can export to any compatible collector. API and persisted schemas never expose their implementation classes.
