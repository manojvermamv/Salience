from salience.contracts.common import CompatibilityError, contract_versions_compatible
from salience.contracts.plugins import (
    CapabilityNotSupported,
    PluginDisabled,
    PluginManifest,
)


class PluginRegistry:
    def __init__(self, *, supported_contract_version: str) -> None:
        self._supported_contract_version = supported_contract_version
        self._manifests: dict[str, dict[str, PluginManifest]] = {}

    def register(self, manifest: PluginManifest) -> PluginManifest:
        if not contract_versions_compatible(
            supported=self._supported_contract_version,
            candidate=manifest.contract_version,
        ):
            raise CompatibilityError(
                f"plugin {manifest.plugin_id} uses incompatible contract {manifest.contract_version}"
            )
        versions = self._manifests.setdefault(manifest.plugin_id, {})
        existing = versions.get(manifest.version)
        if existing is not None and existing != manifest:
            raise CompatibilityError(
                f"plugin {manifest.plugin_id}@{manifest.version} is already registered"
            )
        versions[manifest.version] = manifest
        return manifest

    def resolve(
        self,
        plugin_id: str,
        version: str | None = None,
        *,
        include_disabled: bool = False,
    ) -> PluginManifest:
        versions = self._manifests.get(plugin_id)
        if not versions:
            raise LookupError(plugin_id)
        selected_version = version or max(versions, key=self._version_key)
        manifest = versions[selected_version]
        if manifest.status == "disabled" and not include_disabled:
            raise PluginDisabled(f"plugin {plugin_id}@{selected_version} is disabled")
        return manifest

    def validate(self, plugin_id: str, capability: str, version: str | None = None) -> PluginManifest:
        manifest = self.resolve(plugin_id, version)
        if capability not in manifest.capabilities:
            raise CapabilityNotSupported(capability)
        return manifest

    def disable(self, plugin_id: str, version: str) -> PluginManifest:
        manifest = self.resolve(plugin_id, version, include_disabled=True)
        disabled = manifest.model_copy(update={"status": "disabled"})
        self._manifests[plugin_id][version] = disabled
        return disabled

    def history(self, plugin_id: str) -> tuple[PluginManifest, ...]:
        versions = self._manifests.get(plugin_id, {})
        return tuple(versions[version] for version in sorted(versions, key=self._version_key))

    def manifests(self, *, include_disabled: bool = False) -> tuple[PluginManifest, ...]:
        manifests: list[PluginManifest] = []
        for plugin_id in sorted(self._manifests):
            for manifest in self.history(plugin_id):
                if include_disabled or manifest.status != "disabled":
                    manifests.append(manifest)
        return tuple(manifests)

    @staticmethod
    def _version_key(version: str) -> tuple[int, ...]:
        try:
            return tuple(int(part) for part in version.split("."))
        except ValueError as error:
            raise CompatibilityError(f"invalid plugin version: {version}") from error
