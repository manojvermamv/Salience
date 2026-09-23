from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


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
