# ADR 0006: Governed publisher adapters and private YouTube sessions

- Status: Accepted
- Date: 2026-09-13

## Context

An approved `ReadyToPublishPackage@v1` must not implicitly publish. Publishing
adds account authority, a platform destination, current policy/rights/
disclosure constraints, cost, and an irreversible remote effect. The platform
must survive interruption after acceptance without duplicating that effect and
must retain audit, provenance, trace, schedule, receipt, and reconciliation
facts independently from a provider SDK.

The Phase 9 direction requests official-API preference. The selected provider
boundary is the official YouTube Data API, with independently replaceable
YouTube/TikTok/Instagram/LinkedIn/future adapters, explicit budget identity,
and no credential persistence in canonical records. The official
[YouTube Data API `videos.insert` reference](https://developers.google.com/youtube/v3/docs/videos/insert)
requires an OAuth upload scope. Its [resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
starts a `POST /upload/youtube/v3/videos?uploadType=resumable` session and
returns an opaque `Location` URI for the subsequent byte transfer.

## Decision

- Retain canonical PostgreSQL publisher accounts, secret-reference-only
  connections, capability profiles, immutable requests/plans/schedules/attempts,
  append-only status/webhook facts, remote receipts, and publication records.
- Keep provider behavior behind the owned, versioned `PublisherAdapter`
  contract and `PublisherRegistry`; provider metadata controls selection rather
  than conditional branches in canonical publication logic.
- Run publisher effects in `GovernedPublicationWorkflow`. It requires a distinct
  publication approval and re-authorizes ready-package/account/profile/policy/rights/
  disclosure/approval/budget/quota facts immediately before remote submission,
  writes an effect plan before the remote boundary, reconciles the same
  idempotency key after ambiguity or restart, and records a safe receipt before
  terminal publication.
- Adopt a small direct `httpx` YouTube adapter, not a Google SDK or a custom
  workflow/queue/object-storage/auth/tracing subsystem. Existing PostgreSQL,
  Temporal, owned credential lease, delivery, tracing, and registry boundaries
  remain the mature/reusable components for those concerns.
- Make `YouTubePublisherAdapter` disabled by default and private-only. It may
  start a resumable session only with an injected, current `youtube.upload`
  lease, explicit connection reference, and durable edge-session store. The store keeps
  the bearer-adjacent opaque URI outside canonical data; a SHA-256 session
  identity is the safe DTO projection and restart reconciliation key.
- Fail generic YouTube submission closed until a future explicit edge
  media-handoff can stream package bytes, retain provider-specific transfer
  reconciliation, calculate real usage, and satisfy a separate enablement gate.

## Consequences

The deterministic fixture path proves exactly one accepted remote effect after
crash/restart, bounded retries/timeouts/dead letters, cancellation, duplicate
webhook convergence, policy/budget/approval denial, and complete audit/
provenance/trace/cost records. It does not claim a configured live publisher.
The YouTube adapter proves the official private session-start request and
edge-store session reconciliation after adapter replacement under a mocked
transport; its opt-in live status reports `NOT RUN` without operator
configuration. Other platform adapters can be registered or removed without
changing canonical publication records.

The trade-off is intentional: there is no current media transfer or live-video
smoke execution. Production enablement needs an operator-managed secret/lease
resolver, approved edge uploader, private test asset, platform policy review,
actual-cost mapping, provider reconciliation, and release evidence.
