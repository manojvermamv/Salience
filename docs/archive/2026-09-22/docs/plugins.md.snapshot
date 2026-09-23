# Versioned Plugin Registry

The registry owns plugin identity and compatibility history. It does not instantiate
a provider SDK or authorize an external effect.

Each `PluginManifest` records:

- stable plugin ID and immutable version;
- contract major version;
- capabilities and effect classification;
- required secret scopes;
- provider metadata;
- MCP/A2A or other protocol compatibility metadata;
- enabled/disabled status.

Registration rejects incompatible contract majors. Disabling replaces the active
manifest status but retains the version in history, preserving provenance and
reconciliation for prior jobs. Resolution excludes disabled plugins by default; audit
or recovery code may explicitly resolve a historical disabled version.

The initial registry remains in-memory to keep the capability contract portable.
The canonical schema reserves `plugin_versions` and `plugin_capabilities` for a
repository adapter without changing public registry semantics. No plugin is
required by the Phase 1–9 fixture path. The creative capability registry resolves
only enabled, compatible candidates by declared capability, modality, and
explicit provider constraints. Fixture providers prove this selection/replacement
boundary; the credential-gated Synthesia REST adapter is an optional owned DTO
edge, not a default runtime dependency. Model, MCP, A2A, research, creative, and
future publishing providers remain independently versioned boundaries.
