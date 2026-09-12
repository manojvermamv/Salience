# Salience

> Find what people care about. Create what they can’t ignore.

**Salience is an operator-controlled, durable foundation for content programs.**
It gives a small team a governed way to define workspaces and content programs,
run restart-safe work, inspect every effect, and add future providers without
making one model, agent, tool, or platform the system of record.

**Status: Phases 1–6 intelligence loop implemented.** A dry-run program can
now move through source-linked research, signals, ranked opportunities,
strategy, strategic packages, claim/evidence checks, and immutable
`ContentBrief` records. It still stops before scripting, media, publishing,
analytics, and learning loops.

[Open the interactive Archify architecture map](docs/salience-phase-1-4.architecture.html)

## Architecture At A Glance

```mermaid
flowchart LR
    Operator[Operators, API clients, CLI, SDK] -->|scoped control request| API[Control API]
    API -->|canonical identity and governance| PG[(PostgreSQL)]
    API -->|start durable run| Temporal[Temporal]
    Temporal -->|tasks and timers| Worker[Salience worker]
    Worker -->|checkpoint, audit, provenance| PG
    Worker -->|reconciled idempotent effect| Mock[Mock effect provider]
    Worker -.->|contracted bytes| Storage[Object-store contract]
    API -->|authorized calls| Agents[Agent registry]
    Agents -->|pinned compatibility| Protocols[Model, MCP, A2A, plugin contracts]
    Bootstrap[Bounded niche bootstrap] -.->|evidence, memory, strategy| PG
```

The Mermaid view is a quick GitHub-native overview. The linked
[Archify viewer](docs/salience-phase-1-4.architecture.html) is the checked,
interactive source for the Phase 1–4 architecture: it supports theme switching,
focus views, relationship tracing, and local SVG/PNG export.

## What You Can Rely On Today

### A Canonical Operational Core

- **Workspace and content-program identity** lives in PostgreSQL with immutable,
  migration-managed records.
- **Jobs, schedules, timers, checkpoints, retries, timeouts, and dead letters**
  are owned data and workflow contracts, not provider-specific application state.
- **Durable execution** runs through Temporal behind a project-owned adapter;
  a long-running dummy job resumes safely after worker or container interruption.
- **External effects are reconciled** through idempotency keys and durable effect
  records, so an accepted mocked request is not performed twice after recovery.
- **Object bytes stay replaceable** behind a memory/S3-compatible object-storage
  contract. Garage is the self-hostable S3 deployment option, not a hard-coded
  data dependency.

### Governance Before Effects

- Token authentication and explicit permission scopes protect control operations.
- Policy references, approvals, dry-run mode, and budget checks happen before an
  effect is attempted.
- Estimated and reserved costs have durable hooks alongside actual-cost entries.
- Secret references remain separate from secret values and are constrained by
  permission scopes.
- Each run records audit events, provenance, idempotency/reconciliation state,
  distributed run identities, and OpenTelemetry-compatible trace identifiers.

### Provider-Neutral Extensions

| Surface | Shipped foundation | Intentionally deferred |
| --- | --- | --- |
| Agents | Versioned manifests, direct/delegated fixture calls, and teams | Autonomous content agents and delegated production authority |
| Models | Structured static/OpenAI-compatible gateways and invocation lineage | A required model vendor |
| MCP | Official SDK adapter for `2026-07-28` plus legacy negotiation | Unneeded optional extensions |
| A2A | Official SDK adapter for A2A `1.0` plus explicit `0.3` behavior | Mandatory remote agents |
| Research and strategy | RSS/HN/browser contracts, signals, packages, claims, and briefs | Crawling, scripting, media, publishing, and learning |
| Plugins | Versioned capability registry and provider-compatibility metadata | Large platform integrations |

The data model also keeps hooks for tenant isolation, agent/tool trust and
delegated authority, jurisdiction policy, privacy classification and retention,
content-origin/C2PA metadata, and future protocol-version compatibility.

## Run A Safe Demo

### Prerequisites

- Docker Engine with Docker Compose v2
- Python `3.13` only when running the local test suite

The default demo uses a fixture-only external-effect provider. It does **not**
publish content or call an AI provider.

```bash
git clone https://github.com/manojvermamv/Salience.git
cd Salience

cp .env.example .env
# Edit .env and set a long, unique CONTROL_PLANE_TOKEN.

docker compose --profile application build
docker compose up -d postgres temporal
docker compose --profile application run --rm migrate
docker compose --profile application up -d api worker mock-effect-provider
```

The Compose deployment keeps the control API inside the project network rather
than publishing a host port. Use the included CLI from the running API
container to start and inspect the safe default dry-run:

```bash
docker compose exec api content jobs start-dummy \
  --idempotency-key getting-started-001

# Copy job_id from the JSON response.
docker compose exec api content jobs inspect <job_id>
docker compose exec api content agents list
docker compose logs -f worker
```

`content jobs start-dummy` defaults to `--dry-run`. The configured Temporal
control plane rejects non-dry-run calls until an operator supplies the required
policy adapter; direct production effects are not a Phase 1–4 feature.

Stop the local stack when finished:

```bash
docker compose --profile application down --remove-orphans
```

## Public Control Surface

All control endpoints require `Authorization: Bearer <CONTROL_PLANE_TOKEN>` and
the appropriate comma-separated `X-Salience-Scopes` header. `control:write`
creates identities and starts jobs; `control:read` retrieves state and records.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/workspaces` | Create a canonical workspace identity |
| `POST /v1/workspaces/{workspace_id}/programs` | Create a content program in that workspace |
| `POST /v1/jobs/dummy` | Start an idempotent, dry-run-by-default durable job |
| `GET /v1/jobs/{job_id}/inspection` | Retrieve trace, audit, provenance, and cost records together |
| `GET /v1/jobs/{job_id}/audit` | Retrieve the job audit trail |
| `GET /v1/jobs/{job_id}/provenance` | Retrieve provenance records |
| `GET /v1/jobs/{job_id}/costs` | Retrieve reserved/actual cost records |
| `GET /v1/jobs/{job_id}/trace` | Retrieve the distributed trace identity |
| `GET /v1/agents` | List versioned fixture agent capabilities |
| `POST /v1/agents/{agent_id}/runs` | Invoke a validated direct or delegated fixture agent run |
| `POST /v1/intelligence/runs` | Start an idempotent dry-run intelligence workflow |
| `GET /v1/intelligence/runs/{job_id}` | Inspect its state, trace, and output IDs |
| `POST /v1/intelligence/schedules` | Store a read-only research cadence |
| `POST /v1/intelligence/opportunities/{opportunity_id}/briefs` | Start a selected-opportunity brief workflow |
| `GET /v1/intelligence/briefs/{brief_id}` | Retrieve immutable ContentBrief content and lineage |

The command-line client and Python SDK both use these public schemas; neither
reaches into the database.

## Verify The Intelligence Loop

Create the local development environment, then run the isolated Phase 1–4
verification stack:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

bash scripts/verify-phases-1-4.sh
```

The Foundation verifier provisions a disposable PostgreSQL and Temporal project, applies
all migrations, and exercises Phase 1 recovery/control, Phase 2 callable
agents, Phase 3 protocol contracts, Phase 4 bootstrap behavior, and the
cross-phase contract. The recovery case deliberately hard-exits a worker after
the fixture accepts an effect, then proves safe reconciliation after restart.

## What Is Deliberately Not Here

Salience is not presented as a finished autonomous content system. This phase
does not include:

- AI content or media generation providers
- Publishing automation or broad platform integrations
- Browser-driven production research
- Learning, analytics, or optimization loops
- Production MCP, A2A, or model-provider SDK transports
- A production secret-manager deployment or Garage cluster configuration

Keeping these capabilities out of the foundation is intentional: later systems
must attach through the governed, versioned contracts above rather than bypass
identity, budget, policy, audit, or provenance controls.

For the focused Phase 5–6 verifier and optional real-feed configuration, see
[`docs/verification.md`](docs/verification.md) and [`docs/research.md`](docs/research.md).

## Repository Guide

| Start here | Contents |
| --- | --- |
| [`docs/core/README.md`](docs/core/README.md) | Product direction, target architecture, acceptance criteria, and phased plan |
| [`docs/architecture.md`](docs/architecture.md) | Canonical architecture decisions and adapter boundaries |
| [`docs/deployment.md`](docs/deployment.md) | Deployment topology and operator responsibilities |
| [`docs/verification.md`](docs/verification.md) | Phase verification approach and evidence |
| [`docs/limitations.md`](docs/limitations.md) | Explicit current constraints and deferred production work |
| [`docs/research.md`](docs/research.md) | Source order, RSS/HN/browser constraints, and live-smoke setup |
| [`docs/trust-model.md`](docs/trust-model.md) | Untrusted-input and memory-write rules |
| [`docs/model-execution.md`](docs/model-execution.md) | Structured model gateway and lineage behavior |
| [`docs/phase-7-handoff.md`](docs/phase-7-handoff.md) | The only allowed input to future scripting work |
| [`docs/implementation-progress.md`](docs/implementation-progress.md) | Persistent build checkpoints and resume context |
| [`docs/adr/`](docs/adr/) | Build-vs-adopt decisions for durable runtime, storage, API persistence, and governance |
| [`docs/contracts/adapter-contracts.md`](docs/contracts/adapter-contracts.md) | Project-owned adapter and compatibility contracts |
| [`Build Phases 1–4 End-to-End.md`](Build%20Phases%201%E2%80%934%20End-to-End.md) | Executed end-to-end implementation checklist |

## Design Principle

**PostgreSQL is the record of truth; providers are replaceable execution
adapters.** Every later agent, model, tool, protocol, storage service, or
publishing destination must cross a versioned Salience contract and leave a
governed, inspectable record behind.
