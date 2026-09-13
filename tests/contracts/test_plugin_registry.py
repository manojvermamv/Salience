import pytest

from salience.contracts.plugins import (
    CompatibilityError,
    PluginDisabled,
    PluginManifest,
)
from salience.plugins.registry import PluginRegistry


def manifest(**overrides: object) -> PluginManifest:
    values = {
        "plugin_id": "mock.external.effect",
        "version": "1.0.0",
        "contract_version": "1.0",
        "capabilities": ["external.effect.execute"],
        "effect_classification": "write",
        "protocol_compatibility": {"mcp": "2026-07-28", "a2a": "1.0"},
    }
    values.update(overrides)
    return PluginManifest.model_validate(values)


def test_registry_rejects_incompatible_contract_version() -> None:
    registry = PluginRegistry(supported_contract_version="1.0")

    with pytest.raises(CompatibilityError):
        registry.register(manifest(contract_version="999.0"))


def test_registry_preserves_disabled_history_and_validates_capabilities() -> None:
    registry = PluginRegistry(supported_contract_version="1.0")
    registered = registry.register(manifest())

    assert registry.resolve("mock.external.effect").version == "1.0.0"
    assert registry.validate("mock.external.effect", "external.effect.execute") == registered

    registry.disable("mock.external.effect", "1.0.0")
    with pytest.raises(PluginDisabled):
        registry.resolve("mock.external.effect")
    assert registry.resolve("mock.external.effect", include_disabled=True).status == "disabled"
    assert registry.history("mock.external.effect") == (registered.model_copy(update={"status": "disabled"}),)


def test_registry_lists_only_enabled_versions_by_default() -> None:
    registry = PluginRegistry(supported_contract_version="1.0")
    registry.register(manifest(plugin_id="creative.fixture", version="1.0.0"))
    registry.register(manifest(plugin_id="creative.fixture", version="1.1.0"))
    registry.disable("creative.fixture", "1.1.0")

    assert [item.version for item in registry.manifests()] == ["1.0.0"]
    assert [item.version for item in registry.manifests(include_disabled=True)] == ["1.0.0", "1.1.0"]


def test_creative_registry_rejects_provider_metadata_without_lifecycle_selection_facts() -> None:
    from salience.creative.capabilities import CreativeCapabilityRegistry

    registry = CreativeCapabilityRegistry(PluginRegistry(supported_contract_version="1.0"))
    incomplete = manifest(
        plugin_id="fixture.creative.video",
        capabilities=["text_to_video"],
        provider_metadata={
            "supported_capabilities": ["text_to_video"],
            "modalities": ["video"],
            "formats": ["video/mp4"],
            "aspect_ratios": ["9:16"],
            "minimum_duration_seconds": 1,
            "maximum_duration_seconds": 60,
            "async_support": True,
            "polling_support": True,
            "webhook_support": True,
            "cancellation_support": True,
            "reconciliation_support": True,
            "enabled": True,
            "contract_compatibility": {"creative": "1.0"},
            "max_concurrency": 1,
            "estimated_cost_micros": 0,
            "limitations": ["fixture only"],
        },
    )

    with pytest.raises(ValueError, match="rate_state"):
        registry.register(incomplete)
