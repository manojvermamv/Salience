# Control API

The control API requires a configured bearer `CONTROL_PLANE_TOKEN` and an
`X-Salience-Scopes` header. Reads require `control:read`; mutations require
`control:write`. Health endpoints are unauthenticated.

- `GET /health/live` and `GET /health/ready`
- `POST /v1/workspaces` and `POST /v1/workspaces/{workspace_id}/programs`
- `POST /v1/jobs/dummy` (dry-run defaults to `true`)
- `GET /v1/jobs/{job_id}`, `/inspection`, `/audit`, `/provenance`, `/costs`, and `/trace`

`POST /v1/jobs/dummy` accepts an idempotency key. The deployed adapter returns
the same canonical job for repeat keys rather than scheduling another workflow.
Non-dry-run jobs are intentionally denied until a configured policy adapter is
present.

The `content` CLI calls this same HTTP surface. For example:

```bash
export CONTROL_PLANE_TOKEN='local-development-token'
export SALIENCE_CONTROL_URL='http://127.0.0.1:8000'
content jobs start-dummy --idempotency-key local-check
```
