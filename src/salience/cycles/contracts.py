from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from salience.contracts.plugins import PluginManifest


class GoalSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["GoalSpec.local.v1"] = "GoalSpec.local.v1"
    objective: str = Field(min_length=1, max_length=2000)
    metric_versions: tuple[str, ...] = Field(min_length=1, max_length=20)
    audience: str = Field(min_length=1, max_length=256)
    account_refs: tuple[str, ...] = Field(min_length=1, max_length=20)
    brand_scope: str = Field(min_length=1, max_length=256)
    source_policy: Literal["fixture-only"]
    horizon_end: AwareDatetime
    cadence_seconds: int = Field(default=3600, ge=1, le=31536000)
    max_concurrent: int = Field(default=1, ge=1, le=10)
    max_cycles: int = Field(default=10, ge=1, le=100)
    max_wakes: int = Field(default=3, ge=1, le=10)
    review_required: bool = False
    dry_run: Literal[True] = True
    max_spend: Literal[0] = 0
    provider: Literal["fixture.dummy@1.0.0"] = "fixture.dummy@1.0.0"
    fallback: Literal["deny"] = "deny"

    @field_validator("metric_versions", "account_refs")
    @classmethod
    def bounded_references(cls, values):
        if any(not value.strip() or len(value) > 256 for value in values) or len(set(values)) != len(values):
            raise ValueError("references must be unique bounded nonempty values")
        return values


class CadencePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor: AwareDatetime
    interval_seconds: int = Field(default=60, ge=1, le=31536000)
    overlap: Literal["forbid", "bounded"] = "forbid"
    catch_up: Literal["skip", "latest", "bounded"] = "latest"
    max_catch_up: int = Field(default=3, ge=1, le=100)
    max_pending: int = Field(default=10, ge=1, le=100)
    stale_after_seconds: int = Field(default=300, ge=1, le=86400)
    backfill_seconds: int = Field(default=3600, ge=1, le=604800)
    intent_ttl_seconds: int = Field(default=7200, ge=1, le=604800)
    event_freshness_seconds: int = Field(default=300, ge=1, le=86400)
    coalescing: Literal["goal-utc-slot"] = "goal-utc-slot"

    @model_validator(mode="after")
    def valid_windows(self):
        if self.anchor.microsecond or self.backfill_seconds < self.stale_after_seconds:
            raise ValueError("whole-second anchor and bounded backfill window required")
        if self.intent_ttl_seconds <= self.backfill_seconds:
            raise ValueError("intent lifetime must exceed backfill window")
        return self


class GoalSpecV2(GoalSpec):
    schema_version: Literal["GoalSpec.local.v2"] = "GoalSpec.local.v2"
    content_program_id: UUID
    channel_refs: tuple[Literal["fixture-channel"], ...] = Field(min_length=1, max_length=1)
    account_refs: tuple[Literal["fixture-account"], ...] = Field(min_length=1, max_length=1)
    content_scope: Literal["fixture-only"]
    policy_refs: tuple[str, ...] = Field(min_length=1, max_length=20)
    domain_policy_refs: tuple[str, ...] = ()
    jurisdiction_policy_refs: tuple[str, ...] = ()
    data_classification: Literal["internal"] = "internal"
    permitted_uses: tuple[Literal["local-fixture"], ...] = ("local-fixture",)
    retention_policy: str = Field(min_length=1, max_length=256)
    cadence: CadencePolicy
    cadence_seconds: None = None
    research_limit: Literal[0] = 0
    generation_limit: Literal[0] = 0
    exploration_allocation: Literal[0] = 0
    approval_threshold: Literal["every-baseline-and-backfill"] = "every-baseline-and-backfill"
    stop_criteria: tuple[Literal["horizon", "cycle-quota", "authority-revocation"], ...] = ("horizon", "cycle-quota", "authority-revocation")
    wall_time_seconds: int = Field(default=60, ge=1, le=60)
    tool_calls: Literal[0] = 0
    credential_refs: tuple[()] = ()
    agent_trust: Literal["untrusted-no-tools"] = "untrusted-no-tools"
    delegated_authority: Literal["none"] = "none"
    template_version: Literal["fixture.noop@1.0.0"] = "fixture.noop@1.0.0"

    @field_validator("policy_refs", "domain_policy_refs", "jurisdiction_policy_refs")
    @classmethod
    def policy_references(cls, values):
        return cls.bounded_references(values)

    @model_validator(mode="after")
    def no_implicit_capabilities(self):
        if self.cadence.overlap == "forbid" and self.max_concurrent != 1:
            raise ValueError("forbid overlap requires max_concurrent=1")
        if set(self.stop_criteria) != {"horizon", "cycle-quota", "authority-revocation"} or len(self.stop_criteria) != 3:
            raise ValueError("all baseline stop criteria required exactly once")
        if self.cadence.anchor >= self.horizon_end:
            raise ValueError("cadence anchor must precede horizon")
        return self


class CycleRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: Literal["manual", "event", "scheduled"]
    idempotency_key: str = Field(min_length=1, max_length=256, pattern=r"\S")
    expected_revision: int = Field(ge=1)
    slot_time: AwareDatetime
    event_at: AwareDatetime | None = None
    event_id: str | None = Field(default=None, min_length=1, max_length=256)
    backfill: bool = False
    predecessor_cycle_id: UUID | None = None

    @model_validator(mode="after")
    def event_binding(self):
        if self.origin == "event":
            if self.event_at is None or self.event_id is None:
                raise ValueError("event time and source identity required")
        elif self.event_at is not None or self.event_id is not None:
            raise ValueError("event metadata requires event origin")
        return self


def parse_goal(payload):
    return (GoalSpecV2 if payload.get("schema_version") == "GoalSpec.local.v2" else GoalSpec).model_validate(payload)


class AuthoritySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: UUID
    workspace_id: UUID
    scope: Literal["cycles:write"]
    identity_revision: int = Field(ge=1)
    identity_expires_at: AwareDatetime
    grant_id: UUID
    grant_expires_at: AwareDatetime
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunContextV2(GoalSpecV2):
    schema_version: Literal["RunContext.local.v2"] = "RunContext.local.v2"
    goal_schema_version: Literal["GoalSpec.local.v2"]
    context_id: UUID
    goal_id: UUID
    goal_revision: int = Field(ge=1)
    intent_id: UUID
    cycle_id: UUID
    operation_id: UUID
    workspace_id: UUID
    tenant_id: UUID | None
    subject_id: UUID
    authority: AuthoritySnapshot
    schedule_revision: int = Field(ge=1)
    business_slot: str = Field(min_length=1, max_length=256)
    baseline_approval_id: UUID
    strategy_version: Literal["fixture.baseline@1.0.0"]
    prompt_version: Literal["fixture.noop@1.0.0"]
    capability_manifest: PluginManifest
    evidence_cutoff: AwareDatetime
    evidence_snapshot_refs: tuple[()]
    execution_deadline: AwareDatetime
    recorded_at: AwareDatetime
    event_at: AwareDatetime
    traceparent: str = Field(pattern=r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$")
    correlation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    causation_id: UUID
    lineage_refs: tuple[UUID, UUID]
    assignment_id: None
    arm_id: None
    plan_id: None
    origin: Literal["fixture"]
    c2pa_manifest_ref: None
    production_effects_enabled: Literal[False]

    @model_validator(mode="after")
    def bound_identity_and_deadline(self):
        if self.authority.subject_id != self.subject_id or self.authority.workspace_id != self.workspace_id:
            raise ValueError("authority must bind context scope")
        if self.schedule_revision != self.goal_revision or self.causation_id != self.intent_id:
            raise ValueError("context revision and causation must be canonical")
        if not self.evidence_cutoff < self.execution_deadline <= self.horizon_end:
            raise ValueError("bounded execution deadline required")
        return self
