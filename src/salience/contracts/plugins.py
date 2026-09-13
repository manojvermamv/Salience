from pydantic import BaseModel, ConfigDict, Field, model_validator

from salience.contracts.common import CompatibilityError


class PluginDisabled(RuntimeError):
    pass


class CapabilityNotSupported(LookupError):
    pass


class PluginManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    plugin_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    contract_version: str = Field(min_length=1, max_length=64)
    capabilities: tuple[str, ...] = Field(min_length=1)
    effect_classification: str = Field(min_length=1, max_length=64)
    protocol_compatibility: dict[str, str] = Field(default_factory=dict)
    required_secret_scopes: frozenset[str] = frozenset()
    provider_metadata: dict[str, object] = Field(default_factory=dict)
    status: str = "enabled"

    @model_validator(mode="after")
    def _validate_creative_provider_metadata(self) -> "PluginManifest":
        creative_capabilities = {
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
        if not creative_capabilities.intersection(self.capabilities):
            return self
        required_keys = {
            "modalities",
            "formats",
            "async_support",
            "polling_support",
            "webhook_support",
            "max_concurrency",
            "estimated_cost_micros",
            "limitations",
        }
        missing = required_keys.difference(self.provider_metadata)
        if missing:
            raise ValueError(
                f"provider_metadata missing creative keys: {', '.join(sorted(missing))}"
            )
        return self


__all__ = [
    "CapabilityNotSupported",
    "CompatibilityError",
    "PluginDisabled",
    "PluginManifest",
]
