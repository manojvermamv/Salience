"""Capability-first selection of creative provider plugins."""

from salience.contracts.plugins import CapabilityNotSupported, PluginManifest
from salience.creative.contracts import CreativeCapabilityRequest
from salience.plugins.registry import PluginRegistry


class CreativeCapabilityRegistry:
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def register(self, manifest: PluginManifest) -> PluginManifest:
        return self._registry.register(manifest)

    def resolve(self, request: CreativeCapabilityRequest) -> PluginManifest:
        candidates = [
            manifest
            for manifest in self._registry.manifests()
            if request.capability in manifest.capabilities
            and request.expected_modality in _strings(manifest.provider_metadata, "modalities")
            and (request.provider_id is None or manifest.plugin_id == request.provider_id)
        ]
        if not candidates:
            raise CapabilityNotSupported(request.capability)
        return max(candidates, key=lambda manifest: _version_key(manifest.version))


def _strings(metadata: dict[str, object], key: str) -> frozenset[str]:
    value = metadata.get(key, ())
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        return frozenset()
    return frozenset(value)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))
