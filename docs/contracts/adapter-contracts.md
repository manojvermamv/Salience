# Adapter Contracts

Salience owns these interfaces. Adapters may depend on a provider SDK, but canonical
models, jobs, and agent records may not.

## Object Storage

`ObjectStore` supports `put`, `get`, and `delete`. A successful put returns a
portable `ObjectReceipt` containing the logical key, SHA-256 checksum, byte size,
content type, and caller metadata.

- `MemoryObjectStore` is the deterministic contract-test implementation.
- `S3ObjectStore` accepts an S3 client (including Garage, AWS S3, or another
  compatible implementation) by dependency injection.
- The S3 adapter writes only bytes and object metadata to the object store. It reports
  the owned receipt to an `ArtifactRecorder`, which is the boundary later backed by
  the canonical `artifacts` table.

The adapter treats a missing S3 object as `ObjectNotFound`; callers do not depend on
a provider exception class. Artifact bytes never become the only source of canonical
metadata.

The Phase 5–6 browser and model-recording paths retain artifact references rather
than provider response objects. Object storage remains optional for fixture-backed
research and no browser binary is required by the default intelligence loop.

## Workflow

`WorkflowBackend` is intentionally small: start, status, cancel, and resume. The
Temporal adapter is implemented in the durable-workflow task. Keeping this protocol
here prevents Temporal workflow handles and exceptions from entering job or API
contracts.

`IntelligenceLoopWorkflow` consumes this same boundary. Its source, ranking,
agent, strategy, package, claim, and brief stages use canonical checkpoints and
idempotent repositories, so a backend retry does not become a second semantic
decision or external effect.

## Compatibility

Adapter contracts are semantically versioned. A plugin with a different major contract
version is rejected at registration time. This is a deliberate safe failure: operators
must upgrade, pin, or provide a compatibility adapter instead of silently loading a
semantically incompatible integration.
