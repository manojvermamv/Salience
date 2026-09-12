from pydantic import BaseModel, ConfigDict, Field

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


__all__ = [
    "CapabilityNotSupported",
    "CompatibilityError",
    "PluginDisabled",
    "PluginManifest",
]

