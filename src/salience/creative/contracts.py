"""Versioned provider-neutral contracts for creative production."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    max_variants: int = Field(gt=0, le=16)
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
    state: Literal["submitted", "running", "completed", "failed", "cancelled"]
    failure_class: str | None = Field(default=None, max_length=128)
    download_reference: str | None = None
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
