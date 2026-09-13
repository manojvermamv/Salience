# Phase 9 Governed Publishing

The only valid Phase-9 publishing input is an immutable
`ReadyToPublishPackage@v1`. It is produced only after the Phase 7–8 creative
workflow has retained its selected `ContentBrief@v1`, script version, creative
brief, storyboard/shot plan, provider job history, assets, platform profile,
distribution package, title/thumbnail decision, localization, originality
evaluation, synthetic-media disclosure decision, approval decision, and trace
lineage. Approved decisions are immutable: an exact replay retains the same
version, while changed governed input must create a new distribution and
ready-package version before it can be considered for publishing.

`ReadyToPublishPackage@v1` intentionally contains no account identifier,
publisher credential, post target, scheduling request, or remote publishing
receipt. Its `approval_state` is literally `approved`; it is a governed handoff,
not a command to publish.

## Implemented contract

The fixture-first publishing capability now:

- accepts only a resolved `ReadyToPublishPackage@v1` and its immutable package
  lineage; it must never rebuild creative or research decisions from scratch;
- adds a separate, versioned publisher request and account/credential boundary;
- re-authorizes the proposed publish effect for the selected profile, locale,
  territory, policy version, scopes, approval, and remaining budget;
- persists an idempotent external-effect plan before calling a publisher, then
  reconciles the same key after timeout, retry, or process interruption;
- records the remote receipt, audit event, provenance record, cost settlement,
  and W3C/OpenTelemetry-compatible trace links without mutating the ready
  package;
- provides a fixture-first recovery verifier before a live provider is enabled.

## Explicit non-inheritance

Creative approval does not confer publishing authority. A Phase 9 implementation
must not infer a social account, expand scopes, reuse a secret outside its
declared scope, bypass a new approval requirement, or treat a fixture-provider
asset as live-published media.

## Current boundary

Phase 9 adds publisher/account/profile identities, immutable request/plan/
schedule/attempt/receipt facts, policy/approval/budget reauthorization, scoped
control API/CLI/SDK, fixture reconciliation, and signed-webhook ingress. The
fixture path is explicitly configured for the end-to-end verifier; the deployed
control plane still defaults to dry-run and does not create a ready package.

`YouTubePublisherAdapter` is disabled by default and private-only. It uses the
official YouTube Data API to start a resumable session from an injected scoped
lease, but no bearer token or opaque session URI enters canonical records. It
uses an injected edge session store to reconcile the safe session identity after
adapter replacement, but does not yet stream package bytes or create a video.
Its live status remains `NOT RUN` without explicit operator configuration.
TikTok, Instagram, LinkedIn, and future providers remain independently
selectable/replaceable registry adapters rather than branches in canonical
publication logic. Optional FFmpeg execution and C2PA signing are represented
by adapters and provenance state, but remain `NOT RUN` unless a separate
signer-backed media integration is executed.
