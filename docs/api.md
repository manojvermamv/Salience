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
