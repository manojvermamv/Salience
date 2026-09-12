# Phases 1–4 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a Docker-deployable, restart-safe, framework-neutral foundation through niche bootstrap, with no Phase-5 content-production functionality.

**Architecture:** PostgreSQL remains the canonical system of record; Temporal supplies durable task queues, retries, timers, and workflow replay behind an owned `WorkflowBackend`. A Python service exposes control, agent, and bootstrap APIs; a separate worker runs Temporal activities. Garage provides the S3-compatible byte service behind an owned object-store contract; model runtimes, MCP tools, A2A agents, policy, secrets, and identity all enter through owned, versioned contracts.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy/Alembic, PostgreSQL, Temporal Python SDK/server, Garage S3 endpoint with boto3, OpenTelemetry API/SDK, JSON Schema, Docker Compose, pytest.

**Spec:** `Build Phases 1–4 End-to-End.md`, `docs/core/`

## Global Constraints

- Run on Python 3.13 and Docker 29; record exact resolved versions, licenses, security posture, operational cost, and exit paths in ADRs before use.
- Keep PostgreSQL canonical; all external IDs are mappings, never primary identities.
- Keep Temporal, S3, MCP, A2A, model, and framework types outside persisted/API domain records.
- Default all external-effect paths to dry-run; fixtures never make network writes or use real credentials.
- Use UUID primary keys, explicit parent/run/trace identities, typed JSON-schema boundaries, and nullable future tenant boundaries.
- Exclude Phase-5 signals, strategic packaging, scripting, media production, publishing, analytics, and learning loops.
- Each task starts with a focused failing test, proves red, implements the minimum, proves green, and records a checkpoint commit.

---

### Task 1: Establish the deployable project and reuse decisions

**Files:**
- Create: `pyproject.toml`, `Dockerfile`, `compose.yaml`, `.env.example`, `.gitignore`, `src/salience/__init__.py`, `src/salience/config.py`, `docs/dependencies.md`
- Create: `docs/adr/0001-durable-runtime.md`, `docs/adr/0002-object-storage.md`, `docs/adr/0003-control-api-and-persistence.md`, `docs/adr/0004-policy-secrets-and-observability.md`, `tests/test_config.py`

**Interfaces:**
- Produces `Settings.from_environment() -> Settings` and a Compose stack with PostgreSQL, Temporal, SeaweedFS, API, and worker services.

- [x] **Step 1: Write the failing configuration test**
```python
def test_settings_require_nonempty_control_token() -> None:
    with pytest.raises(ValidationError):
        Settings.from_mapping({"CONTROL_PLANE_TOKEN": ""})
```
- [x] **Step 2: Verify red**
Run: `pytest tests/test_config.py::test_settings_require_nonempty_control_token -q`
Expected: FAIL because `Settings` does not exist.
- [x] **Step 3: Implement configuration and deployment files**
```python
@dataclass(frozen=True)
class Settings:
    database_url: str
    temporal_target: str
    s3_endpoint_url: str
    control_plane_token: SecretReference

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings": ...
```
Pin resolved Python packages and container image digests; make the worker/API wait for dependency health checks. ADRs must select Temporal and Garage only after recording current version, license, maintenance/security evidence, adapter boundary, fallback, and data-export path. Record why a scoped environment resolver is sufficient for Phase 1 while an OpenBao adapter remains the production secret-manager extension point; record why OPA is deferred behind `PolicyEngine` for the narrow deterministic rules in scope.
- [x] **Step 4: Verify green**
Run: `pytest tests/test_config.py -q && docker compose config -q`
Expected: PASS and a valid Compose configuration.
- [x] **Step 5: Checkpoint**
Run: `git add pyproject.toml Dockerfile compose.yaml .env.example .gitignore src/salience/config.py docs tests/test_config.py && git commit -m "chore: establish phase foundation runtime"`

### Task 2: Create the canonical PostgreSQL model and migrations

**Files:**
- Create: `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_canonical_foundation.py`
- Create: `src/salience/db/base.py`, `src/salience/db/models.py`, `src/salience/db/session.py`, `src/salience/core/ids.py`
- Create: `tests/integration/test_migrations.py`, `docs/database.md`

**Interfaces:**
- Produces UUID-backed `Workspace`, `ContentProgram`, `Job`, `Checkpoint`, `ExternalEffect`, `AuditEvent`, `ProvenanceRecord`, `Budget`, `CostLedgerEntry`, `PolicyVersion`, `Approval`, `PluginVersion`, `SecretReferenceRecord`, and `Artifact` models.

- [x] **Step 1: Write failing migration assertion**
```python
async def test_initial_migration_creates_workspace_program_and_job_tables(db):
    tables = await list_tables(db)
    assert {"workspaces", "content_programs", "jobs", "job_checkpoints"} <= tables
```
- [x] **Step 2: Verify red**
Run: `pytest tests/integration/test_migrations.py::test_initial_migration_creates_workspace_program_and_job_tables -q`
Expected: FAIL because no migration exists.
- [x] **Step 3: Implement migration and metadata**
```python
class CanonicalIdentity(Base):
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[UUID | None]
    created_at: Mapped[datetime]
```
The migration must create required FK/index/unique constraints, including `(workspace_id, external_system, external_id)`, external-effect idempotency keys, monotonic audit sequence per run, and budget reservation references. Add `data_classification`, retention/deletion fields, domain-policy references, trust/delegation fields, C2PA/provenance extensions, trace/span IDs, and protocol compatibility JSON to appropriate canonical rows. Never store secret values.
- [x] **Step 4: Verify green**
Run: `docker compose up -d postgres && alembic upgrade head && pytest tests/integration/test_migrations.py -q`
Expected: PASS against real PostgreSQL.
- [x] **Step 5: Checkpoint**
Run: `git add alembic.ini migrations src/salience/db src/salience/core/ids.py docs/database.md tests/integration/test_migrations.py && git commit -m "feat: add canonical postgres foundation"`

### Task 3: Implement audit, provenance, tracing, secrets, permissions, policy, and cost controls

**Files:**
- Create: `src/salience/governance/audit.py`, `src/salience/governance/costs.py`, `src/salience/governance/policy.py`, `src/salience/governance/scopes.py`, `src/salience/governance/secrets.py`, `src/salience/observability/tracing.py`
- Create: `tests/unit/test_policy.py`, `tests/unit/test_costs.py`, `tests/unit/test_secrets.py`, `tests/unit/test_tracing.py`
- Create: `docs/governance.md`

**Interfaces:**
- Produces `PolicyEngine.authorize(request) -> PolicyDecision`, `BudgetService.reserve(...) -> Reservation`, `BudgetService.settle(...) -> CostLedgerEntry`, `SecretResolver.resolve(reference, scopes) -> SecretValue`, and `TraceContext.new_child(...) -> TraceContext`.

- [x] **Step 1: Write failing governance tests**
```python
def test_effect_requires_scope_policy_approval_and_budget(governance):
    decision = governance.authorize(effect="mock.write", scopes=set(), estimated_micros=10)
    assert decision.allowed is False

def test_budget_settlement_releases_reservation(governance):
    reservation = governance.reserve("run-1", 50)
    assert governance.settle(reservation.id, 30).actual_micros == 30
```
- [x] **Step 2: Verify red**
Run: `pytest tests/unit/test_policy.py tests/unit/test_costs.py -q`
Expected: FAIL because governance services do not exist.
- [x] **Step 3: Implement deterministic governance services**
```python
@dataclass(frozen=True)
class AuthorizationRequest:
    subject_id: UUID
    required_scopes: frozenset[str]
    effect_class: EffectClass
    policy_version_id: UUID
    dry_run: bool
```
Policy evaluation must deny unknown scopes, expired/disabled policies, budget overflow, and required-but-unapproved effects. Record decision/audit/provenance in one transaction. Generate and persist W3C-compatible trace IDs while emitting matching OpenTelemetry spans; redact secret values from every event. Store estimated, reserved, released, and actual cost rows separately.
- [x] **Step 4: Verify green**
Run: `pytest tests/unit/test_policy.py tests/unit/test_costs.py tests/unit/test_secrets.py tests/unit/test_tracing.py -q`
Expected: PASS with redaction and denial cases covered.
- [x] **Step 5: Checkpoint**
Run: `git add src/salience/governance src/salience/observability tests/unit docs/governance.md && git commit -m "feat: add deterministic governance controls"`

### Task 4: Implement storage, capability registry, and adapter contracts

**Files:**
- Create: `src/salience/contracts/common.py`, `src/salience/contracts/storage.py`, `src/salience/contracts/workflow.py`, `src/salience/contracts/plugins.py`, `src/salience/storage/s3.py`, `src/salience/storage/memory.py`, `src/salience/plugins/registry.py`
- Create: `tests/contracts/test_object_store.py`, `tests/contracts/test_plugin_registry.py`, `docs/contracts/adapter-contracts.md`, `docs/plugins.md`

**Interfaces:**
- Produces `ObjectStore.put/get/delete`, `WorkflowBackend.start/status/cancel/resume`, and `PluginRegistry.register/resolve/validate`.

- [x] **Step 1: Write failing object-store and registry contracts**
```python
def object_store_contract(store: ObjectStore) -> None:
    receipt = store.put(key="a.txt", data=b"phase-1", content_type="text/plain")
    assert store.get(receipt.key).data == b"phase-1"

def test_registry_rejects_incompatible_contract_version(registry):
    with pytest.raises(CompatibilityError):
        registry.register(PluginManifest(contract_version="999.0"))
```
- [x] **Step 2: Verify red**
Run: `pytest tests/contracts/test_object_store.py tests/contracts/test_plugin_registry.py -q`
Expected: FAIL because contracts and adapters do not exist.
- [x] **Step 3: Implement contract-owned adapters**
```python
class ObjectStore(Protocol):
    def put(self, *, key: str, data: bytes, content_type: str, metadata: Mapping[str, str]) -> ObjectReceipt: ...

class PluginManifest(BaseModel):
    plugin_id: str
    version: str
    contract_version: str
    capabilities: list[str]
    protocol_compatibility: dict[str, str]
```
The S3 adapter persists artifact checksums and metadata in PostgreSQL and uses Garage only for bytes. Registry records provider/model/tool/agent protocol metadata and supports disabled plugins without deleting history.
- [x] **Step 4: Verify green**
Run: `pytest tests/contracts/test_object_store.py tests/contracts/test_plugin_registry.py -q`
Expected: PASS for memory and S3 adapters, including incompatibility cases.
- [x] **Step 5: Checkpoint**
Run: `git add src/salience/contracts src/salience/storage src/salience/plugins tests/contracts docs/contracts docs/plugins.md && git commit -m "feat: add storage and plugin contracts"`

### Task 5: Implement Phase-1 durable jobs, effects, and recovery

**Files:**
- Create: `src/salience/workflows/temporal_backend.py`, `src/salience/workflows/jobs.py`, `src/salience/workflows/effects.py`, `src/salience/workflows/worker.py`, `src/salience/workflows/schedules.py`, `src/salience/fixtures/mock_effect_provider.py`
- Create: `tests/integration/test_durable_dummy_job.py`, `tests/e2e/test_worker_restart.py`, `docs/workflows.md`, `docs/recovery.md`

**Interfaces:**
- Produces `DummyWorkflowRequest`, `JobService.start_dummy`, `ExternalEffectService.execute_or_reconcile`, and `TemporalWorkflowBackend`.

- [x] **Step 1: Write the failure/recovery test before workflow code**
```python
def test_killed_worker_resumes_checkpoint_without_duplicate_effect(compose_stack):
    job = compose_stack.start_dummy_job(crash_after="provider_effect")
    compose_stack.kill("worker")
    compose_stack.start("worker")
    assert compose_stack.wait_for_job(job.id).status == "succeeded"
    assert compose_stack.effect_calls(job.id) == 1
```
- [x] **Step 2: Verify red**
Run: `pytest tests/e2e/test_worker_restart.py::test_killed_worker_resumes_checkpoint_without_duplicate_effect -q`
Expected: FAIL because the worker and workflow are absent.
- [x] **Step 3: Implement Temporal-backed workflow boundary**
```python
class ExternalEffectService:
    async def execute_or_reconcile(self, request: EffectRequest) -> EffectReceipt:
        existing = await self.repository.completed_or_external_receipt(request.idempotency_key)
        if existing:
            return existing
        return await self._reconcile_then_execute_once(request)
```
Use Temporal task queues, retry policy, start-to-close timeouts, durable timers/schedules, cancellation, signal/query status, and an exhausted-retry terminal transition to canonical dead-letter state. Activities store a canonical checkpoint before every external boundary. The independent mock provider must deduplicate its own idempotency key and expose reconciliation; kill a real Compose worker after remote acceptance but before local receipt persistence.
- [x] **Step 4: Verify green**
Run: `docker compose up -d && alembic upgrade head && pytest tests/integration/test_durable_dummy_job.py tests/e2e/test_worker_restart.py -q`
Expected: PASS after actual worker termination/restart, retry exhaustion, timeout, cancellation, approval, budget, and dry-run cases.
- [ ] **Step 5: Checkpoint**
Run: `git add src/salience/workflows src/salience/fixtures tests/integration/test_durable_dummy_job.py tests/e2e/test_worker_restart.py docs/workflows.md docs/recovery.md && git commit -m "feat: add restart-safe phase one workflows"`

### Task 6: Expose the Phase-1 control plane and checkpoint its verifier

**Files:**
- Create: `src/salience/api/app.py`, `src/salience/api/dependencies.py`, `src/salience/api/routes/control.py`, `src/salience/api/routes/health.py`, `src/salience/api/schemas.py`, `src/salience/cli.py`
- Create: `tests/integration/test_control_api.py`, `tests/e2e/test_phase1_verifier.py`, `docs/api.md`, `docs/local-development.md`

**Interfaces:**
- Produces authenticated `/health/live`, `/health/ready`, workspace/program CRUD, job start/status/cancel/resume, policy/approval, plugin, audit, provenance, cost, and trace inspection endpoints.

- [ ] **Step 1: Write failing API assertion**
```python
def test_admin_can_start_and_inspect_dummy_job(client):
    response = client.post("/v1/jobs/dummy", headers=admin_headers(), json={"dry_run": True})
    assert response.status_code == 202
    assert client.get(f"/v1/jobs/{response.json()['job_id']}", headers=admin_headers()).status_code == 200
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/integration/test_control_api.py::test_admin_can_start_and_inspect_dummy_job -q`
Expected: FAIL because the ASGI application is absent.
- [x] **Step 3: Implement authenticated API and CLI**
```python
def require_scope(required: str) -> Callable[[Request], RequestContext]: ...

@router.post("/v1/jobs/dummy", status_code=202)
async def start_dummy(request: DummyJobRequest, context: AdminContext) -> JobResponse: ...
```
Require a configured control-plane token and request scopes; do not expose raw secrets. CLI commands must call the same HTTP/API schema as external users.
- [x] **Step 4: Verify green and checkpoint Phase 1**
Run: `pytest tests/integration/test_control_api.py tests/e2e/test_phase1_verifier.py -q`
Expected: PASS for the complete Phase-1 acceptance flow, including audit/provenance/trace/cost inspection.
- [ ] **Step 5: Checkpoint**
Run: `git add src/salience/api src/salience/cli.py tests/integration/test_control_api.py tests/e2e/test_phase1_verifier.py docs/api.md docs/local-development.md && git commit -m "feat: expose phase one control plane"`

### Task 7: Add canonical callable-agent entities and registry

**Files:**
- Create: `migrations/versions/0002_agents.py`, `src/salience/agents/contracts.py`, `src/salience/agents/registry.py`, `src/salience/agents/repository.py`
- Create: `tests/unit/test_agent_registry.py`, `tests/integration/test_agent_migration.py`, `docs/agents.md`

**Interfaces:**
- Produces `AgentManifest`, `AgentVersion`, `AgentRun`, `AgentDelegation`, `TeamManifest`, `TeamVersion`, `AgentResult`, and `AgentEvent`.

- [ ] **Step 1: Write failing canonical registry test**
```python
def test_manifest_identity_is_independent_of_runtime(registry):
    manifest = registry.describe("research_agent")
    assert manifest.agent_id == "research_agent"
    assert manifest.runtime_id is None
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/unit/test_agent_registry.py::test_manifest_identity_is_independent_of_runtime -q`
Expected: FAIL because no agent types exist.
- [x] **Step 3: Implement models and migration**
```python
class AgentManifest(BaseModel):
    agent_id: str
    version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tool_scopes: list[str]
    memory_scopes: list[str]
    effect_classification: EffectClass
    supports_sync: bool
    supports_async: bool
```
Persist immutable versions, status, health, parent/child IDs, runtime selection, artifacts, and events. Keep model/framework/provider fields only in execution/provenance mappings.
- [x] **Step 4: Verify green**
Run: `alembic upgrade head && pytest tests/unit/test_agent_registry.py tests/integration/test_agent_migration.py -q`
Expected: PASS with disabled versions preserving history.
- [ ] **Step 5: Checkpoint**
Run: `git add migrations/versions/0002_agents.py src/salience/agents tests/unit/test_agent_registry.py tests/integration/test_agent_migration.py docs/agents.md && git commit -m "feat: add canonical callable agent registry"`

### Task 8: Implement native agent execution, Lead Agent, specialists, and teams

**Files:**
- Create: `src/salience/agents/execution.py`, `src/salience/agents/lead.py`, `src/salience/agents/specialists.py`, `src/salience/agents/teams.py`
- Create: `tests/unit/test_agent_execution.py`, `tests/integration/test_agent_delegation.py`, `tests/e2e/test_phase2_verifier.py`

**Interfaces:**
- Produces `AgentService.invoke(request) -> AgentRun`, `LeadContentAgent.bootstrap(...)`, and `TeamRunner.invoke(...)`.

- [ ] **Step 1: Write failing direct/delegated equivalence test**
```python
async def test_lead_uses_same_research_contract_as_direct_user(agent_service):
    direct = await agent_service.invoke(AgentInvocation(agent_id="research_agent", input={"niche": "finance"}))
    delegated = await agent_service.invoke_from_parent("lead_content_agent", direct.request)
    assert direct.result_schema == delegated.result_schema
    assert delegated.parent_run_id is not None
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/integration/test_agent_delegation.py::test_lead_uses_same_research_contract_as_direct_user -q`
Expected: FAIL because native execution is absent.
- [x] **Step 3: Implement native runners**
```python
class AgentRuntime(Protocol):
    async def invoke(self, invocation: AgentInvocation, context: AgentExecutionContext) -> AgentResult: ...
```
Register durable `lead_content_agent`, `research_agent`, and `strategy_agent`; permit sync calls only below the manifest timeout and use the WorkflowBackend for async calls. Apply scopes, policies, budgets, memory filters, cancellation, retries, trace parentage, and JSON Schema validation at this shared boundary. Teams compose existing agents through the same service.
- [x] **Step 4: Verify green**
Run: `pytest tests/unit/test_agent_execution.py tests/integration/test_agent_delegation.py tests/e2e/test_phase2_verifier.py -q`
Expected: PASS for status/events/cancel/resume/restart, direct/delegated lineage, missing specialist, and team member direct invocation.
- [ ] **Step 5: Checkpoint Phase 2**
Run: `git add src/salience/agents tests/unit/test_agent_execution.py tests/integration/test_agent_delegation.py tests/e2e/test_phase2_verifier.py && git commit -m "feat: run framework-neutral callable agents"`

### Task 9: Add agent REST, CLI, and Python SDK surfaces

**Files:**
- Create: `src/salience/api/routes/agents.py`, `src/salience/sdk/__init__.py`, `src/salience/sdk/client.py`, `tests/integration/test_agent_api.py`, `tests/unit/test_sdk.py`
- Modify: `src/salience/cli.py`, `src/salience/api/app.py`, `docs/api.md`, `docs/agents.md`

**Interfaces:**
- Produces `GET /v1/agents`, `GET /v1/agents/{agent_id}`, `POST /v1/agents/{agent_id}/runs`, run inspection/cancel/resume/events endpoints, and `SalienceClient.agents.run(...)`.

- [ ] **Step 1: Write failing public-client test**
```python
def test_client_lists_describes_and_runs_research_agent(api_server):
    client = SalienceClient(api_server.url, api_server.token)
    assert client.agents.describe("research_agent").agent_id == "research_agent"
    assert client.agents.run("research_agent", {"niche": "finance"}).status in {"succeeded", "queued"}
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/integration/test_agent_api.py tests/unit/test_sdk.py -q`
Expected: FAIL because the client/endpoints are absent.
- [x] **Step 3: Implement API-compatible clients**
```python
class AgentsClient:
    def run(self, agent_id: str, input: Mapping[str, Any], *, mode: Literal["sync", "async"] = "async") -> AgentRunView: ...
```
The CLI commands `content agents list|describe|run|status|cancel` must use these canonical HTTP requests and never call an internal-only path.
- [x] **Step 4: Verify green**
Run: `pytest tests/integration/test_agent_api.py tests/unit/test_sdk.py -q`
Expected: PASS for sync/async/client/CLI schemas.
- [ ] **Step 5: Checkpoint**
Run: `git add src/salience/api/routes/agents.py src/salience/sdk src/salience/cli.py tests/integration/test_agent_api.py tests/unit/test_sdk.py docs && git commit -m "feat: expose callable agent clients"`

### Task 10: Implement model gateway and runtime interchangeability

**Files:**
- Create: `src/salience/models/contracts.py`, `src/salience/models/gateway.py`, `src/salience/models/static.py`, `src/salience/models/openai_compatible.py`
- Create: `tests/contracts/test_model_gateway.py`, `tests/integration/test_runtime_swap.py`, `docs/model-gateway.md`

**Interfaces:**
- Produces `ModelGateway.complete(request) -> ModelResult`, `StaticModelAdapter`, `OpenAICompatibleAdapter`, and capability/usage metadata.

- [ ] **Step 1: Write failing portability test**
```python
async def test_same_agent_runs_through_two_runtime_configurations(agent_service):
    first = await agent_service.invoke_with_runtime("research_agent", "static-v1", {"niche": "finance"})
    second = await agent_service.invoke_with_runtime("research_agent", "static-v2", {"niche": "finance"})
    assert first.agent_id == second.agent_id == "research_agent"
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/contracts/test_model_gateway.py tests/integration/test_runtime_swap.py -q`
Expected: FAIL because no model gateway exists.
- [ ] **Step 3: Implement owned model boundary**
```python
class ModelGateway(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResult: ...
```
The static adapter must produce deterministic valid/invalid fixture payloads. The OpenAI-compatible adapter must map plain HTTP JSON only, validate structured output before persistence, capture token/cost/latency, and fail cleanly without credentials. Agent manifests request capabilities/policy, never provider classes.
- [ ] **Step 4: Verify green**
Run: `pytest tests/contracts/test_model_gateway.py tests/integration/test_runtime_swap.py -q`
Expected: PASS for two configurations, fallback, timeout, disabled provider, and invalid structured output.
- [ ] **Step 5: Checkpoint**
Run: `git add src/salience/models tests/contracts/test_model_gateway.py tests/integration/test_runtime_swap.py docs/model-gateway.md && git commit -m "feat: add provider-neutral model gateway"`

### Task 11: Implement MCP tool gateway and fixture

**Files:**
- Create: `src/salience/mcp/contracts.py`, `src/salience/mcp/gateway.py`, `src/salience/mcp/fixture_server.py`
- Create: `tests/integration/test_mcp_gateway.py`, `docs/mcp-a2a.md`

**Interfaces:**
- Produces `ToolGateway.discover/invoke`, `ToolManifest`, and fixture `niche.lookup` tool.

- [ ] **Step 1: Write failing MCP lifecycle test**
```python
async def test_mcp_gateway_records_provenanced_tool_call(gateway):
    tool = await gateway.discover("fixture-mcp", "niche.lookup")
    result = await gateway.invoke(tool, {"niche": "personal finance"})
    assert result.output["source"] == "fixture"
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/integration/test_mcp_gateway.py -q`
Expected: FAIL because the MCP gateway is absent.
- [ ] **Step 3: Implement current-spec adapter**
```python
class ToolGateway(Protocol):
    async def discover(self, server_id: str, tool_name: str) -> ToolManifest: ...
    async def invoke(self, manifest: ToolManifest, arguments: Mapping[str, Any]) -> ToolResult: ...
```
Pin the verified MCP SDK/spec compatibility in plugin metadata, retrieve tool schemas, enforce scope/timeout/schema validation, and persist canonical tool run/audit/provenance records. The fixture uses no external account.
- [ ] **Step 4: Verify green**
Run: `pytest tests/integration/test_mcp_gateway.py -q`
Expected: PASS for discovery, schema, invocation, permission denial, and timeout.
- [ ] **Step 5: Checkpoint**
Run: `git add src/salience/mcp tests/integration/test_mcp_gateway.py docs/mcp-a2a.md && git commit -m "feat: add mcp tool gateway fixture"`

### Task 12: Implement A2A remote-agent gateway and fixture

**Files:**
- Create: `src/salience/a2a/contracts.py`, `src/salience/a2a/gateway.py`, `src/salience/a2a/fixture_agent.py`
- Create: `tests/integration/test_a2a_gateway.py`
- Modify: `docs/mcp-a2a.md`

**Interfaces:**
- Produces `RemoteAgentGateway.discover/invoke`, canonical `RemoteAgentDescriptor`, and a versioned fixture card/task endpoint.

- [ ] **Step 1: Write failing remote-agent test**
```python
async def test_a2a_fixture_maps_result_and_parent_lineage(gateway, parent_run):
    agent = await gateway.discover("fixture-a2a")
    result = await gateway.invoke(agent, {"niche": "finance"}, parent_run.id)
    assert result.parent_run_id == parent_run.id
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/integration/test_a2a_gateway.py -q`
Expected: FAIL because the A2A adapter is absent.
- [ ] **Step 3: Implement current-compatible A2A boundary**
```python
class RemoteAgentGateway(Protocol):
    async def discover(self, endpoint: str) -> RemoteAgentDescriptor: ...
    async def invoke(self, descriptor: RemoteAgentDescriptor, input: Mapping[str, Any], parent_run_id: UUID) -> AgentResult: ...
```
Use the current verified A2A SDK/spec mapping at the transport edge; convert card/task/artifact state into canonical records, preserve child provenance, and reject incompatible version ranges before invocation. The fixture exposes discoverable skills and an asynchronous task result.
- [ ] **Step 4: Verify green and checkpoint Phase 3**
Run: `pytest tests/integration/test_a2a_gateway.py tests/integration/test_mcp_gateway.py tests/integration/test_runtime_swap.py -q`
Expected: PASS for A2A success, async result, artifact mapping, incompatibility, and adapter removal readability.
- [ ] **Step 5: Checkpoint**
Run: `git add src/salience/a2a tests/integration/test_a2a_gateway.py docs/mcp-a2a.md && git commit -m "feat: add a2a remote agent fixture"`

### Task 13: Add scoped memory and bootstrap domain records

**Files:**
- Create: `migrations/versions/0003_memory_strategy.py`, `src/salience/memory/contracts.py`, `src/salience/memory/repository.py`, `src/salience/bootstrap/contracts.py`, `src/salience/bootstrap/repository.py`
- Create: `tests/integration/test_scoped_memory.py`, `docs/memory.md`

**Interfaces:**
- Produces `MemoryRecord`, `MemoryScope`, `StrategyVersion`, `ResearchEvidence`, and scope-filtered `MemoryRepository.retrieve`.

- [ ] **Step 1: Write failing scope-isolation test**
```python
async def test_memory_retrieval_never_returns_another_programs_records(repository):
    await repository.record(program_id="one", scope="semantic", content={"audience": "A"})
    assert await repository.retrieve(program_id="two", scopes={"semantic"}) == []
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/integration/test_scoped_memory.py -q`
Expected: FAIL because memory tables and repository are absent.
- [ ] **Step 3: Implement versioned memory/strategy persistence**
```python
class MemoryRecordInput(BaseModel):
    scope: Literal["working", "semantic", "evidence", "episodic", "analytics", "artifact"]
    content: dict[str, Any]
    trust_level: str
    confidence: Decimal
    evidence_ids: list[UUID]
```
Persist source, trust, confidence, verification/expiry, supersession/conflict, writer identity, sensitivity, provenance, and program/tenant boundaries. Do not add a vector database.
- [ ] **Step 4: Verify green**
Run: `alembic upgrade head && pytest tests/integration/test_scoped_memory.py -q`
Expected: PASS for scope, evidence, provenance, classification, and retention fields.
- [ ] **Step 5: Checkpoint**
Run: `git add migrations/versions/0003_memory_strategy.py src/salience/memory src/salience/bootstrap tests/integration/test_scoped_memory.py docs/memory.md && git commit -m "feat: add scoped durable memory"`

### Task 14: Implement bounded bootstrap research and versioned strategy generation

**Files:**
- Create: `src/salience/research/contracts.py`, `src/salience/research/fixtures.py`, `src/salience/bootstrap/service.py`, `src/salience/bootstrap/lead_workflow.py`
- Create: `tests/unit/test_bootstrap_service.py`, `tests/e2e/test_phase4_verifier.py`, `docs/bootstrap.md`

**Interfaces:**
- Produces `ContentProgramService.create_from_niche`, `ResearchConnector.research`, and `BootstrapResult`.

- [ ] **Step 1: Write failing niche-only end-to-end test**
```python
def test_niche_only_bootstrap_creates_explainable_strategy_and_memory(client):
    result = client.content_programs.create(niche="Personal Finance")
    assert result.strategy.content_pillars
    assert result.assumptions
    assert result.evidence
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/e2e/test_phase4_verifier.py::test_niche_only_bootstrap_creates_explainable_strategy_and_memory -q`
Expected: FAIL because the bootstrap service is absent.
- [ ] **Step 3: Implement a bounded, fixture-backed research flow**
```python
class ResearchConnector(Protocol):
    async def research(self, request: BootstrapResearchRequest) -> list[ResearchFinding]: ...
```
The Lead Agent starts a durable bootstrap run, calls `research_agent` through `AgentService`, persists sources/fetch times/provenance, invokes `strategy_agent`, records provisional audiences/positioning/pillars/channels/formats/metrics/uncertainties as immutable `StrategyVersion`, and writes only scope-authorized durable memory. Fixture research is the default clean-deployment mode; HTTP/browser connectors remain optional later adapters.
- [ ] **Step 4: Verify green**
Run: `pytest tests/unit/test_bootstrap_service.py tests/e2e/test_phase4_verifier.py -q`
Expected: PASS for niche-only run, direct research call, interrupted/restarted bootstrap, evidence provenance, assumptions, and scoped retrieval.
- [ ] **Step 5: Checkpoint Phase 4**
Run: `git add src/salience/research src/salience/bootstrap tests/unit/test_bootstrap_service.py tests/e2e/test_phase4_verifier.py docs/bootstrap.md && git commit -m "feat: bootstrap program strategy from niche"`

### Task 15: Complete operations documentation and the cross-phase verifier

**Files:**
- Create: `scripts/verify-phases-1-4.sh`, `tests/e2e/test_phases_1_4_stack.py`, `docs/architecture.md`, `docs/deployment.md`, `docs/verification.md`, `docs/limitations.md`
- Modify: `README.md`, `docs/dependencies.md`, `docs/api.md`

**Interfaces:**
- Produces a repeatable clean-stack verifier that exercises durable jobs, agent execution, protocol fixtures, and niche bootstrap.

- [ ] **Step 1: Write the cross-phase failure test**
```python
def test_clean_stack_executes_phases_one_through_four(compose_stack):
    report = compose_stack.run_cross_phase_verifier()
    assert report.phase_1_recovery and report.phase_2_agents
    assert report.phase_3_protocols and report.phase_4_bootstrap
```
- [ ] **Step 2: Verify red**
Run: `pytest tests/e2e/test_phases_1_4_stack.py -q`
Expected: FAIL until each phase exposes its verifier result.
- [ ] **Step 3: Implement verifier script and runbooks**
```bash
docker compose up -d --build
alembic upgrade head
pytest tests/e2e/test_phases_1_4_stack.py -q
```
The script must create fresh named volumes, wait for readiness, run migration/health/restart/reconciliation/agent/model/MCP/A2A/bootstrap checks, print retained IDs for inspection, then remove only its uniquely named test project. Document architecture, exact dependencies/ADRs, contracts, migration model, API/CLI/SDK usage, recovery, and Phase-5+ exclusions.
- [ ] **Step 4: Verify green**
Run: `scripts/verify-phases-1-4.sh`
Expected: PASS on a clean Docker project with no skipped required assertions.
- [ ] **Step 5: Final checkpoint**
Run: `git add scripts tests/e2e docs README.md && git commit -m "test: verify phases one through four end to end"`

## Plan Self-Review

- Coverage: Tasks 1–6 satisfy Phase 1; Tasks 7–9 satisfy Phase 2; Tasks 10–12 satisfy Phase 3; Tasks 13–14 satisfy Phase 4; Task 15 validates the integrated deployment.
- Dependency control: Task 1 records adoption/exit decisions before custom infrastructure is implemented; Tasks 4, 5, 10, 11, and 12 enforce owned adapter boundaries.
- Verifier coverage: every phase has an explicit red/green test, and Task 15 executes the required clean-stack sequence including a real worker kill/restart.
- Scope: no task creates Phase-5 signals, media generation, publishing, analytics, or learning loops.
