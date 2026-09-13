import pytest
from pydantic import ValidationError

from salience.contracts.plugins import CapabilityNotSupported, PluginManifest
from salience.plugins.registry import PluginRegistry


def creative_manifest(**overrides: object) -> PluginManifest:
    values = {
        "plugin_id": "fixture.creative.video",
        "version": "1.0.0",
        "contract_version": "1.0",
        "capabilities": ["text_to_video"],
        "effect_classification": "write",
        "provider_metadata": {
            "supported_capabilities": ["text_to_video"],
            "modalities": ["video"],
            "formats": ["video/mp4"],
            "aspect_ratios": ["9:16", "16:9"],
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
            "rate_state": "available",
            "estimated_cost_micros": 0,
            "limitations": ["fixture only"],
        },
    }
    values.update(overrides)
    return PluginManifest.model_validate(values)


def test_script_draft_request_requires_brief_and_claim_links() -> None:
    from salience.creative.contracts import ScriptDraftRequest

    with pytest.raises(ValidationError):
        ScriptDraftRequest(
            content_program_id="program-1",
            brief_id="",
            script_key="script-1",
            target_format="short_video",
            target_duration_seconds=30,
            claim_ids=[],
            evidence_ids=[],
        )


def test_capability_resolution_selects_an_enabled_compatible_provider() -> None:
    from salience.creative.capabilities import CreativeCapabilityRegistry
    from salience.creative.contracts import CreativeCapabilityRequest

    registry = CreativeCapabilityRegistry(PluginRegistry(supported_contract_version="1.0"))
    registered = registry.register(creative_manifest())

    selected = registry.resolve(
        CreativeCapabilityRequest(
            request_key="render-1",
            content_program_id="program-1",
            brief_id="brief-1",
            script_id="script-1",
            capability="text_to_video",
            expected_modality="video",
            aspect_ratio="9:16",
            resolution="1080x1920",
            duration_seconds=30,
            max_variants=3,
        )
    )

    assert selected == registered


def test_capability_resolution_rejects_unsupported_capability() -> None:
    from salience.creative.capabilities import CreativeCapabilityRegistry
    from salience.creative.contracts import CreativeCapabilityRequest

    registry = CreativeCapabilityRegistry(PluginRegistry(supported_contract_version="1.0"))
    registry.register(creative_manifest())

    with pytest.raises(CapabilityNotSupported, match="avatar_video"):
        registry.resolve(
            CreativeCapabilityRequest(
                request_key="render-2",
                content_program_id="program-1",
                brief_id="brief-1",
                script_id="script-1",
                capability="avatar_video",
                expected_modality="video",
                aspect_ratio="16:9",
                resolution="1920x1080",
                duration_seconds=30,
                max_variants=1,
            )
        )


def test_creative_plugin_rejects_incomplete_provider_metadata() -> None:
    with pytest.raises(ValidationError, match="provider_metadata"):
        creative_manifest(provider_metadata={"modalities": ["video"]})


def test_capability_request_rejects_a_variant_count_above_configured_quota() -> None:
    from salience.creative.contracts import CreativeCapabilityRequest

    with pytest.raises(ValidationError, match="max_variants"):
        CreativeCapabilityRequest(
            request_key="render-over-quota",
            content_program_id="program-1",
            brief_id="brief-1",
            script_id="script-1",
            capability="text_to_video",
            expected_modality="video",
            aspect_ratio="9:16",
            resolution="1080x1920",
            duration_seconds=30,
            max_variants=4,
        )


def test_provider_result_preserves_explicit_unknown_actual_cost() -> None:
    from salience.creative.contracts import ProviderJobResult, ProviderUsage

    result = ProviderJobResult(
        provider_id="fixture-creative",
        capability="text_to_video",
        request_key="render-1",
        external_job_id="fixture-1",
        state="submitted",
        usage=ProviderUsage(estimated_micros=100, actual_micros=None),
    )

    assert result.usage.actual_micros is None
    assert result.usage.actual_cost_status == "pending"


def test_creative_variant_plan_rejects_a_variant_outside_its_bounded_plan() -> None:
    from salience.creative.contracts import CreativeVariantPlan

    with pytest.raises(ValidationError, match="variant_index"):
        CreativeVariantPlan(
            request_key="render-1",
            variant_key="render-1:2",
            variant_index=2,
            max_variants=1,
        )


def test_creative_rights_context_is_immutable() -> None:
    from salience.creative.contracts import CreativeRightsContext

    rights = CreativeRightsContext(
        asset_license_ids=("license-1",),
        territory="US",
        channel="video",
        commercial_use=True,
    )

    with pytest.raises(ValidationError):
        rights.territory = "CA"
