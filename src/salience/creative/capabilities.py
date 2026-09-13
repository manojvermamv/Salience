"""Capability-first selection of creative provider plugins."""

from dataclasses import dataclass

from salience.contracts.plugins import CapabilityNotSupported, PluginManifest
from salience.creative.contracts import (
    CreativeCapabilityRequest,
    CreativeProviderCapabilities,
    CreativeSelectionConstraints,
)
from salience.plugins.registry import PluginRegistry


@dataclass(frozen=True)
class CreativeProviderSelection:
    """Selected manifest and safe, deterministic candidate rejections."""

    selected: PluginManifest
    rejected_candidates: dict[str, str]


class CreativeCapabilityRegistry:
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def register(self, manifest: PluginManifest) -> PluginManifest:
        _capabilities(manifest)
        return self._registry.register(manifest)

    def resolve(
        self,
        request: CreativeCapabilityRequest,
        *,
        constraints: CreativeSelectionConstraints | None = None,
    ) -> CreativeProviderSelection:
        constraints = constraints or CreativeSelectionConstraints()
        if request.max_variants > constraints.max_variants:
            raise CapabilityNotSupported("requested variants exceed the configured variant quota")
        rejected: dict[str, str] = {}
        candidates: list[PluginManifest] = []
        for manifest in self._registry.manifests(include_disabled=True):
            reason = _rejection_reason(manifest, request, constraints)
            if reason is None:
                candidates.append(manifest)
            else:
                rejected[f"{manifest.plugin_id}@{manifest.version}"] = reason
        if not candidates:
            if request.provider_id is not None:
                raise CapabilityNotSupported(
                    f"explicit provider {request.provider_id} is unavailable for {request.capability}"
                )
            raise CapabilityNotSupported(request.capability)
        return CreativeProviderSelection(
            selected=max(candidates, key=lambda manifest: _version_key(manifest.version)),
            rejected_candidates=rejected,
        )


def _rejection_reason(
    manifest: PluginManifest,
    request: CreativeCapabilityRequest,
    constraints: CreativeSelectionConstraints,
) -> str | None:
    if request.provider_id is not None and manifest.plugin_id != request.provider_id:
        return "explicit_provider_mismatch"
    if constraints.allowed_provider_ids is not None and manifest.plugin_id not in constraints.allowed_provider_ids:
        return "provider_not_allowed"
    if manifest.status != "enabled":
        return "plugin_disabled"
    capabilities = _capabilities(manifest)
    if not capabilities.enabled:
        return "provider_disabled"
    if request.capability not in manifest.capabilities:
        return "capability_not_declared"
    if request.capability not in capabilities.supported_capabilities:
        return "capability_not_supported"
    if request.expected_modality not in capabilities.modalities:
        return "modality_not_supported"
    if constraints.output_format not in capabilities.formats:
        return "format_not_supported"
    if request.aspect_ratio not in capabilities.aspect_ratios:
        return "aspect_ratio_not_supported"
    if not capabilities.minimum_duration_seconds <= request.duration_seconds <= capabilities.maximum_duration_seconds:
        return "duration_not_supported"
    if not capabilities.async_support or not capabilities.polling_support:
        return "durable_lifecycle_not_supported"
    if constraints.require_webhook and not capabilities.webhook_support:
        return "webhook_not_supported"
    if not capabilities.reconciliation_support:
        return "reconciliation_not_supported"
    if capabilities.rate_state != "available":
        return f"rate_{capabilities.rate_state}"
    if capabilities.max_concurrency < 1:
        return "concurrency_unavailable"
    if capabilities.contract_compatibility.get("creative") != "1.0":
        return "contract_incompatible"
    if (
        constraints.maximum_estimated_cost_micros is not None
        and capabilities.estimated_cost_micros > constraints.maximum_estimated_cost_micros
    ):
        return "budget_exceeded"
    return None


def _capabilities(manifest: PluginManifest) -> CreativeProviderCapabilities:
    capabilities = CreativeProviderCapabilities.model_validate(manifest.provider_metadata)
    if not set(capabilities.supported_capabilities).issubset(manifest.capabilities):
        raise ValueError("provider_metadata supported_capabilities must be declared by the plugin")
    return capabilities


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
