# Bounded parallel agent delegation

This authorized extension adds concurrent independent agent invocations to the existing callable-agent boundary. P1 item 7 remains a completed historical fixture qualification. This work does not release RG0/RG1, enable production effects, merge main, or complete P2–P7.

One declared lead owns one parent run. The parent is a delegation control run: its input is the typed team plan and its terminal output is the ordered team aggregate. It does not call the lead runtime or claim a lead-model response; the lead manifest pins the permissions delegated to children. Each child separately validates its agent input/output schemas. A bounded plan contains at most 16 independent tasks, at most eight concurrent children per team, an absolute deadline of at most 300 seconds, and immutable inputs/agent manifest pins. Each child retains its original run ID, ordinal, trace and parent ID. Tool, memory and delegation permissions are intersections of parent and child declarations; a plan cannot create authority. Effectful agents are rejected. Model-backed runtimes use the existing gateway and its typed output, with no implicit provider fallback. Paid/live-provider qualification remains held; default execution uses no-effects fixtures.

The direct Python team boundary runs members concurrently and preserves declaration order in its results. Durable invocation is separately exposed through explicitly enabled signed fixture API/CLI/SDK commands. PostgreSQL stores the immutable admitted plan, canonical parent/child agent runs, attempt claims, results and events. Multiple worker processes may share that state, with database-enforced team/workspace concurrency and one claim per child. No new broker or automatic infrastructure scaling is introduced.

The worker commits a child claim before calling a runtime. Completed results are reused on restart. An interrupted or timed-out invocation has an unknown outcome and is held rather than blindly called again; queued siblings can continue within current authorization and the original deadline. Failures and partial results remain inspectable. Cancellation prevents new claims and records unresolved in-flight work. Changed input under an existing idempotency key conflicts. Current subject/grant and pinned agent eligibility are rechecked before each claim.

[Executed qualification](parallel-agents-evidence.json) proves actual overlap without timing-only assertions, concurrency caps across replicas, shared lineage and reduced scopes, model output aggregation, mixed success/failure, timeout/cancellation drainage, exact/conflicting retries, signed cross-workspace denial, revocation, bounded input/output, process death with completed/unknown/queued children, preservation-safe additive migration and API/CLI/SDK parity. Final qualification is 45 focused / 540 full non-live PASS with 325 source hashes at `2653d2e`; actual commands/JUnit/log/source hashes are retained in that report. Independent production approval remains NOT RUN.

## Supported local invocation

The direct Python entry point is `TeamRunner(service).invoke(TeamManifest(...), input)`. `LeadContentAgent.run_intelligence()` now delegates research and strategy concurrently with the lead's shared parent. Direct calls retain results in memory; use the durable boundary for restart recovery. Plans declare independent work; dependent stages remain ordered by their existing workflows.

For durable fixture use, migrate to `0035_parallel_agent_teams`, configure the existing signed identity issuer/audience/public-key file and workspace, and give the current fixture identity `agents:delegate` and `agents:read` grants. A lead's additional tool/memory/delegation scopes also require current grants. Client-provided scope headers grant nothing. Keep the API private:

```bash
export SALIENCE_DEPLOYMENT_MODE=fixture
export SALIENCE_EFFECTS_ENABLED=false
export SALIENCE_PARALLEL_AGENT_TEAMS_ENABLED=true
python -m alembic upgrade head
uvicorn salience.api.app:create_configured_app --factory --host 127.0.0.1
```

Start one or more dedicated workers in separate shells with the same `DATABASE_URL` and `SALIENCE_WORKSPACE_ID`; each worker can run up to eight tasks and all share the database cap of at most 16 running children per workspace:

```bash
python -m salience.agents.parallel_worker
```

The signed API accepts `POST /v1/workspaces/{workspace_id}/agent-teams` with `Idempotency-Key`, returns HTTP 202 and the canonical parent/child IDs, and supports `GET .../{run_id}` and `POST .../{run_id}/cancel`. The CLI uses `SALIENCE_CONTROL_URL` and the issuer's `SALIENCE_CONTROL_JWT`. An example fixture plan is:

```json
{
  "team_id": "intelligence",
  "lead_agent_id": "lead_content_agent",
  "tasks": [
    {"key": "research", "agent_id": "research_agent", "input": {"niche": "fixture niche"}},
    {"key": "strategy", "agent_id": "strategy_agent", "input": {"niche": "fixture niche"}}
  ],
  "max_concurrency": 2,
  "timeout_seconds": 30,
  "max_spend_micros": 0
}
```

```bash
content agent-teams submit --workspace-id "$SALIENCE_WORKSPACE_ID" --idempotency-key intelligence-one --plan-json "$(cat /path/to/plan.json)"
content agent-teams inspect --workspace-id "$SALIENCE_WORKSPACE_ID" --run-id "$TEAM_RUN_ID"
content agent-teams cancel --workspace-id "$SALIENCE_WORKSPACE_ID" --run-id "$TEAM_RUN_ID"
```

SDK equivalents are `SalienceClient(url, signed_jwt).agent_teams.submit(workspace_id, plan, idempotency_key=...)`, `.inspect(workspace_id, run_id)` and `.cancel(workspace_id, run_id)`. Status is queued/running/succeeded/partial/failed/cancelled; each ordered child separately exposes queued/running/succeeded/failed/unknown/cancelled/timed_out/held and a sanitized error type. Each child result is limited to 65,536 serialized bytes. HTTP and admitted plan inputs are limited to 8,192 bytes.

`ModelAgentRuntime` accepts an operator-injected existing model gateway and returns its validated output. The signed plan cannot select providers, credentials, paid budgets or fallback. The default API/worker uses fixture runtimes. The qualified model transport uses controlled OpenAI-compatible mock responses and canonical model-invocation records; live LLM quality, provider cost enforcement and production effect qualification remain outstanding. The operator's `zero_cost_fixture=True` declaration is a trust assertion for local injected test gateways, never a price detector or authority for paid calls.

## Recovery and rollback

A claim commits before runtime entry and cannot be renewed or replayed. Absolute plan/manifest deadlines expire it to `unknown`; cancellation or worker shutdown drains cooperative calls and preserves unresolved outcomes. A worker killed between claim and runtime entry is also conservatively unknown. Completed/failed/unknown/held children retain their IDs and results; exact submission retries inspect the original plan and conflicting retries fail. Restarts claim only queued siblings. Subject/grant revocation or changed pinned manifests prevents new claims; workers recheck current eligibility before invocation and while polling active calls.

The workspace row serializes claims across processes. Database triggers enforce team/workspace caps, deadline bounds, immutable identities/results and preservation of linked canonical events/delegations. PostgreSQL task/run/event transitions commit together; the parent stores an ordered aggregate once all children are terminal. Rollback disables the opt-in API and dedicated workers, preserves execution history and keeps compatible readers. Empty migration rollback is tested; populated downgrade refuses to destroy that history. This extension does not change cycle schedules, deployment worker routing or later-phase release gates.
