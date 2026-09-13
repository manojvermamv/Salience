# Phase 9 Handoff

The only valid Phase-9 publishing input is an immutable
`ReadyToPublishPackage@v1`. It is produced only after the Phase 7–8 creative
workflow has retained its selected `ContentBrief@v1`, script version, creative
brief, storyboard/shot plan, provider job history, assets, platform profile,
distribution package, title/thumbnail decision, localization, originality
evaluation, synthetic-media disclosure decision, approval decision, and trace
lineage.

`ReadyToPublishPackage@v1` intentionally contains no account identifier,
publisher credential, post target, scheduling request, or remote publishing
receipt. Its `approval_state` is literally `approved`; it is a governed handoff,
not a command to publish.

## Phase 9 contract

A later publishing capability must:

- accept only a resolved `ReadyToPublishPackage@v1` and its immutable package
  lineage; it must never rebuild creative or research decisions from scratch;
- add a separate, versioned publisher request and account/credential boundary;
- re-authorize the proposed publish effect for the selected profile, locale,
  territory, policy version, scopes, approval, and remaining budget;
- persist an idempotent external-effect plan before calling a publisher, then
  reconcile the same key after timeout, retry, or process interruption;
- record the remote receipt, audit event, provenance record, cost settlement,
  and W3C/OpenTelemetry-compatible trace links without mutating the ready
  package;
- provide its own fixture-first recovery verifier before a live provider is
  enabled.

## Explicit non-inheritance

Creative approval does not confer publishing authority. A Phase 9 implementation
must not infer a social account, expand scopes, reuse a secret outside its
declared scope, bypass a new approval requirement, or treat a fixture-provider
asset as live-published media.

## Current boundary

Phase 7–8 stops at the ready package. It has no social-platform adapter,
publisher credential, platform account model, audience analytics, or learning
loop. The ready-package fixture is explicitly configured for the end-to-end
verifier; the deployed control plane defaults to dry-run and does not create a
ready package. Optional FFmpeg execution and C2PA signing are represented by
adapters and provenance state, but are `NOT RUN` on this checked host because
the required host binaries/signing configuration are absent.
