"""Versioned provider-neutral contracts for creative production."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CREATIVE_CAPABILITIES = frozenset(
    {
        "generate_image",
        "edit_image",
        "text_to_video",
        "image_to_video",
        "reference_guided_video",
        "first_last_frame_video",
        "extend_video",
        "avatar_video",
        "text_to_speech",
        "voice_clone",
        "lip_sync",
        "dub_video",
        "translate_video",
        "music_or_sfx",
        "caption",
        "compose_video",
        "transcode",
        "thumbnail_render",
        "creative_analysis",
        "upscale",
    }
)

ProviderLifecycleState = Literal[
    "planned",
    "submitting",
    "submitted",
    "running",
    "completed",
    "failed",
    "cancelled",
    "dead_lettered",
]


class CreativeRightsContext(BaseModel):
    """Canonical rights facts carried into creative production."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["CreativeRightsContext@v1"] = "CreativeRightsContext@v1"
    asset_license_ids: tuple[str, ...] = ()
    consent_record_ids: tuple[str, ...] = ()
    likeness_record_ids: tuple[str, ...] = ()
    voice_record_ids: tuple[str, ...] = ()
    usage_restriction_ids: tuple[str, ...] = ()
    reference_asset_ids: tuple[str, ...] = ()
    expiry_record_ids: tuple[str, ...] = ()
    revocation_record_ids: tuple[str, ...] = ()
    territory: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=128)
    commercial_use: bool


class CreativeVariantPlan(BaseModel):
    """One bounded, durable variant identity derived from a creative request."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["CreativeVariantPlan@v1"] = "CreativeVariantPlan@v1"
    request_key: str = Field(min_length=1, max_length=255)
    variant_key: str = Field(min_length=1, max_length=255)
    variant_index: int = Field(gt=0)
    max_variants: int = Field(gt=0, le=3)

    @model_validator(mode="after")
    def _validate_variant_index(self) -> "CreativeVariantPlan":
        if self.variant_index > self.max_variants:
            raise ValueError("variant_index must not exceed max_variants")
        return self


class ProviderUsage(BaseModel):
    """Provider cost facts; unknown actual cost is explicit rather than zero."""

    model_config = ConfigDict(frozen=True)

    estimated_micros: int = Field(ge=0)
    actual_micros: int | None = Field(default=None, ge=0)
    actual_cost_status: Literal["pending", "known"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_actual_cost_status(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        actual_micros = values.get("actual_micros")
        expected_status = "known" if actual_micros is not None else "pending"
        actual_cost_status = values.get("actual_cost_status")
        if actual_cost_status is not None and actual_cost_status != expected_status:
            raise ValueError("actual_cost_status does not match actual_micros")
        return {**values, "actual_cost_status": expected_status}


class ProviderWebhookEvent(BaseModel):
    """Verified, credential-free provider delivery projection."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["ProviderWebhookEvent@v1"] = "ProviderWebhookEvent@v1"
    provider_id: str = Field(min_length=1, max_length=128)
    delivery_id: str | None = Field(default=None, min_length=1, max_length=255)
    external_job_id: str = Field(min_length=1, max_length=255)
    state: ProviderLifecycleState
    safe_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage: ProviderUsage | None = None


class CreativeProviderCapabilities(BaseModel):
    """Portable facts required to select and operate a creative provider."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["CreativeProviderCapabilities@v1"] = "CreativeProviderCapabilities@v1"
    supported_capabilities: tuple[str, ...] = Field(min_length=1)
    modalities: tuple[str, ...] = Field(min_length=1)
    formats: tuple[str, ...] = Field(min_length=1)
    aspect_ratios: tuple[str, ...] = Field(min_length=1)
    minimum_duration_seconds: int = Field(gt=0)
    maximum_duration_seconds: int = Field(gt=0)
    async_support: bool
    polling_support: bool
    webhook_support: bool
    cancellation_support: bool
    reconciliation_support: bool
    enabled: bool
    contract_compatibility: dict[str, str] = Field(min_length=1)
    max_concurrency: int = Field(gt=0)
    rate_state: Literal["available", "rate_limited", "unavailable"]
    estimated_cost_micros: int = Field(ge=0)
    limitations: tuple[str, ...]

    @field_validator("aspect_ratios")
    @classmethod
    def _validate_aspect_ratios(cls, ratios: tuple[str, ...]) -> tuple[str, ...]:
        for ratio in ratios:
            numerator, separator, denominator = ratio.partition(":")
            if not separator or not numerator.isdigit() or not denominator.isdigit():
                raise ValueError("aspect_ratios must contain numeric width:height values")
        return ratios

    @model_validator(mode="after")
    def _validate_duration_range(self) -> "CreativeProviderCapabilities":
        if self.minimum_duration_seconds > self.maximum_duration_seconds:
            raise ValueError("minimum_duration_seconds must not exceed maximum_duration_seconds")
        unsupported = set(self.supported_capabilities).difference(CREATIVE_CAPABILITIES)
        if unsupported:
            raise ValueError(f"unsupported creative capabilities: {sorted(unsupported)}")
        return self


class ScriptDraftRequest(BaseModel):
    """A claim-linked request to draft a versioned script from one brief."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["ScriptDraftRequest@v1"] = "ScriptDraftRequest@v1"
    content_program_id: str = Field(min_length=1)
    brief_id: str = Field(min_length=1)
    script_key: str = Field(min_length=1, max_length=255)
    target_format: str = Field(min_length=1, max_length=128)
    target_duration_seconds: int = Field(gt=0, le=21_600)
    claim_ids: tuple[str, ...] = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class CreativeCapabilityRequest(BaseModel):
    """A bounded provider-neutral request for one creative capability."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["CreativeCapabilityRequest@v1"] = "CreativeCapabilityRequest@v1"
    request_key: str = Field(min_length=1, max_length=255)
    content_program_id: str = Field(min_length=1)
    brief_id: str = Field(min_length=1)
    script_id: str = Field(min_length=1)
    capability: str = Field(min_length=1, max_length=128)
    expected_modality: str = Field(min_length=1, max_length=64)
    aspect_ratio: str = Field(pattern=r"^\d+:\d+$")
    resolution: str = Field(pattern=r"^\d+x\d+$")
    duration_seconds: int = Field(gt=0, le=21_600)
    max_variants: int = Field(gt=0, le=3)
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    provider_extension: dict[str, object] = Field(default_factory=dict)

    @field_validator("capability")
    @classmethod
    def _validate_capability(cls, capability: str) -> str:
        if capability not in CREATIVE_CAPABILITIES:
            raise ValueError(f"unsupported creative capability: {capability}")
        return capability


class ProviderJobResult(BaseModel):
    """Provider-neutral lifecycle projection with no credential-bearing fields."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["ProviderJobResult@v1"] = "ProviderJobResult@v1"
    provider_id: str = Field(min_length=1, max_length=128)
    provider_version: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=255)
    capability: str = Field(min_length=1, max_length=128)
    request_key: str = Field(min_length=1, max_length=255)
    external_job_id: str = Field(min_length=1, max_length=255)
    state: ProviderLifecycleState
    failure_class: str | None = Field(default=None, max_length=128)
    download_reference: str | None = None
    usage: ProviderUsage
    metadata: dict[str, object] = Field(default_factory=dict)


class DistributionPackage(BaseModel):
    """Immutable provider-neutral representation of a validated platform package."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["DistributionPackage@v1"] = "DistributionPackage@v1"
    distribution_package_id: str = Field(min_length=1)
    platform_profile_id: str = Field(min_length=1)
    disclosure_decision_id: str = Field(min_length=1)
    selected_title_key: str = Field(min_length=1)
    locale: str = Field(min_length=1, max_length=32)
    asset_ids: tuple[str, ...] = Field(min_length=1)


class ReadyToPublishPackage(BaseModel):
    """The sole immutable Phase-9 input; it contains no publishing credentials or targets."""

    model_config = ConfigDict(frozen=True)

    contract_version: Literal["ReadyToPublishPackage@v1"] = "ReadyToPublishPackage@v1"
    ready_package_id: str = Field(min_length=1)
    distribution_package_id: str = Field(min_length=1)
    platform_profile_id: str = Field(min_length=1)
    disclosure_decision_id: str = Field(min_length=1)
    approval_state: Literal["approved"]
