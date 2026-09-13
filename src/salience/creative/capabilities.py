"""Capability-first selection of creative provider plugins."""

from salience.contracts.plugins import CapabilityNotSupported, PluginManifest
from salience.creative.contracts import CreativeCapabilityRequest, CreativeProviderCapabilities
from salience.plugins.registry import PluginRegistry


class CreativeCapabilityRegistry:
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def register(self, manifest: PluginManifest) -> PluginManifest:
        _capabilities(manifest)
        return self._registry.register(manifest)

    def resolve(self, request: CreativeCapabilityRequest) -> PluginManifest:
        candidates = [
            manifest
            for manifest in self._registry.manifests()
            if request.capability in manifest.capabilities
            and request.capability in _capabilities(manifest).supported_capabilities
            and request.expected_modality in _capabilities(manifest).modalities
            and _capabilities(manifest).enabled
            and (request.provider_id is None or manifest.plugin_id == request.provider_id)
        ]
        if not candidates:
            raise CapabilityNotSupported(request.capability)
        return max(candidates, key=lambda manifest: _version_key(manifest.version))


def _capabilities(manifest: PluginManifest) -> CreativeProviderCapabilities:
    capabilities = CreativeProviderCapabilities.model_validate(manifest.provider_metadata)
    if not set(capabilities.supported_capabilities).issubset(manifest.capabilities):
        raise ValueError("provider_metadata supported_capabilities must be declared by the plugin")
    return capabilities


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
