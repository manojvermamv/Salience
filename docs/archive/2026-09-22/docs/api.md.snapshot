# Control API

The control API requires a configured bearer `CONTROL_PLANE_TOKEN` and an
`X-Salience-Scopes` header. Reads require `control:read`; mutations require
`control:write`. Health endpoints are unauthenticated.

- `GET /health/live` and `GET /health/ready`
- `POST /v1/workspaces` and `POST /v1/workspaces/{workspace_id}/programs`
- `POST /v1/jobs/dummy` (dry-run defaults to `true`)
- `GET /v1/jobs/{job_id}`, `/inspection`, `/audit`, `/provenance`, `/costs`, and `/trace`
- `GET /v1/agents`, `GET /v1/agents/{agent_id}`, and `POST /v1/agents/{agent_id}/runs`
- `POST /v1/intelligence/runs` and `GET /v1/intelligence/runs/{job_id}`
- `POST /v1/intelligence/schedules`
- `POST /v1/intelligence/opportunities/{opportunity_id}/briefs`
- `GET /v1/intelligence/briefs/{brief_id}` and `/lineage`
- `POST /v1/creative/runs` and `GET /v1/creative/runs/{job_id}`
- `GET /v1/creative/runs/{job_id}/script`, `/asset`, and `/package`
- `GET /v1/creative/packages/{ready_package_id}/lineage`
- `POST /v1/publications/requests`, `POST /v1/publications/schedules`
- `GET /v1/publications/runs/{job_id}`, `POST /v1/publications/runs/{job_id}/cancel`
- `POST /v1/publishers/{provider_id}/webhooks`

`POST /v1/jobs/dummy` accepts an idempotency key. The deployed adapter returns
the same canonical job for repeat keys rather than scheduling another workflow.
Non-dry-run jobs are intentionally denied until a configured policy adapter is
present.

The `content` CLI calls this same HTTP surface. For example:

```bash
export CONTROL_PLANE_TOKEN='local-development-token'
export SALIENCE_CONTROL_URL='http://127.0.0.1:8000'
content jobs start-dummy --idempotency-key local-check
content agents run research_agent --niche 'Personal Finance'
```

`POST /v1/intelligence/runs` accepts `IntelligenceRunRequest@v1` with a
workspace ID, content-program ID, niche, idempotency key, and `dry_run` flag.
It starts the same durable workflow used by the worker, returning a canonical
job and trace ID. The inspection response for a completed run contains IDs for
persisted source, signal, opportunity, strategy, package, and `ContentBrief@v1`
records.

The CLI exposes that contract without database access:

```bash
content intelligence start \
  --workspace-id <workspace_id> --program-id <content_program_id> \
  --niche 'Personal Finance' --idempotency-key local-intelligence-check
content intelligence inspect <job_id>
```

## Creative production

`POST /v1/creative/runs` accepts `CreativeProductionRequest@v1`: workspace ID,
content-program ID, selected `ContentBrief@v1` ID, idempotency key, target
platform-profile key/version, one-to-three `max_variants`, and `dry_run`
(default `true`). A non-dry request must name an explicit canonical `budget_id`.
The caller needs `control:write`. The request returns the canonical job ID and
W3C trace ID; a repeat idempotency key returns the same run rather than a second
workflow.

The deployed `TemporalControlPlane` rejects non-dry creative starts unless an
operator explicitly enables an effect configuration. `GET` creative inspection
routes require `control:read` and return only canonical identities plus the
trace/output projection. `POST /v1/creative/providers/{provider_id}/webhooks`
accepts only a provider-verified, credential-free callback projection and
converges duplicate deliveries into one canonical receipt. There is no publish
endpoint.

The CLI and SDK use exactly this transport contract:

```bash
content creative start \
  --workspace-id <workspace_id> --program-id <content_program_id> \
  --brief-id <brief_id> --profile-key <profile_key> \
  --idempotency-key creative-check
content creative inspect <job_id>
content creative package-lineage <ready_package_id>
```

`SalienceClient.creative` exposes equivalent `start`, `inspect`, `script`,
`asset`, `package`, and `package_lineage` calls. `ReadyToPublishPackage@v1` is
inspection data only; it contains no credentials, publisher target, or action
to publish.

## Governed publication

`POST /v1/publications/requests` requires `control:write`, canonical workspace,
content-program, approved ready-package, publisher-account, distinct current
publication-approval, explicit budget, and idempotency identities. It rejects undeclared fields, including token or
secret fields. `GET /v1/publications/runs/{job_id}` requires `control:read` and
returns only canonical IDs, state, trace, and safe output. Scheduling and
cancellation require `control:write`; a schedule names only immutable publication
request, plan, and budget identities. Temporal receives only the resulting
canonical publication-schedule ID and the worker reloads all effect inputs.

The `content publication start|schedule|inspect|cancel` commands and
`SalienceClient.publication` use those same HTTP routes and never access
PostgreSQL. `POST /v1/publishers/{provider_id}/webhooks` has no bearer-token
requirement because the selected adapter verifies the signed delivery. It stores
only a credential-free verified event projection and returns a duplicate-safe
receipt identity. There is no raw credential endpoint, package mutation route,
or public-publish endpoint. The disabled YouTube adapter accepts private
resumable-session requests only at its internal edge; it is not exposed as a
live video-upload API and requires a durable edge-session store.
