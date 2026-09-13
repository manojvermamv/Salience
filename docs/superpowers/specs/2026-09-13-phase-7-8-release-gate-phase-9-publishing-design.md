# Phase 7–8 Release Gate and Phase 9 Governed Publishing Design

**Status:** Approved for implementation on 2026-09-13

## Goal

First make the existing Phase 7–8 creative path release-ready. Only after its
full verification ladder and a fresh independent review are green, add one
fixture-first, governed publishing path from an immutable
`ReadyToPublishPackage@v1` to an immutable canonical `Publication` without
mutating the ready package or bypassing platform authority.

## Non-Negotiable Boundaries

- `main` remains a non-release development checkpoint until the Phase 7–8 gate
  is green. No live creative or publishing effect is enabled before that gate.
- The sole Phase-9 creative input is `ReadyToPublishPackage@v1`. Publishing
  never reconstructs or changes research, evidence, briefs, scripts, assets,
  distribution decisions, or creative approval.
- Creative approval never grants publishing authority. Each publication effect
  receives its own account binding, policy/rights/disclosure/budget/scope
  reauthorization, immutable external-effect plan, and receipt.
- PostgreSQL remains the canonical source for identities, lifecycle, costs,
  approvals, audit, provenance, traces, and remote receipts. Temporal remains
  the replaceable durable-execution adapter; object storage stays private
  behind its owned contract.
- Credentials are references only in canonical records. Raw access tokens,
  refresh tokens, client secrets, cookies, API keys, signed delivery URLs, and
  provider wire objects never enter canonical records, audit, provenance,
  checkpoints, or API responses.
- Fixture paths are deterministic and CI-safe. Live creative/publishing tests
  are opt-in and report `PASS`, `FAIL`, or `NOT RUN: <reason>` exactly.

## Build-versus-Adopt Decisions

| Need | Decision | Boundary and exit path |
| --- | --- | --- |
| Durable jobs, scheduling, retries, cancellation | Continue using Temporal behind project-owned workflow contracts. | Canonical PostgreSQL job/schedule/effect state permits a different backend to replay safe work. |
| Canonical financial accounting | Implement a small PostgreSQL transactional repository; do not adopt a payment ledger because Salience must own effect-to-run provenance and arbitrary provider costs. | `CostReservationRepository` uses the existing `budgets`, `budget_reservations`, and `cost_ledger_entries` records and can later call an accounting export adapter. |
| Creative budget binding | Require an explicit canonical `budget_id` for every non-dry creative request; dry runs reserve no cost. | The control API and `CreativeProductionRequest@v1` carry the ID, and authorization rejects a missing, inactive, out-of-scope, or insufficient budget before provider submission. |
| Webhook delivery | Use FastAPI's existing HTTP control plane plus canonical receipt persistence and provider-owned signature verification. Do not introduce a queue or webhook SaaS. | `CreativeProvider` and `PublisherAdapter` own signature/DTO conversion; receipts are portable canonical rows. |
| OAuth and publisher credentials | Do not build OAuth or store OAuth material. Use a project-owned credential-resolver contract backed by an operator-managed secure resolver; a missing secure resolver disables the real adapter. | Publisher adapters consume an ephemeral credential lease at the edge; a future OpenBao/OIDC/OAuth resolver changes only that boundary. |
| First official publisher | Implement a credential-gated YouTube Data API adapter using existing `httpx`; do not add a Google SDK dependency. | The adapter uses owned DTOs, least-privilege `youtube.upload`, resumable-upload session IDs, and private-only capability metadata until an operator records a verified audit state. A future SDK can replace its HTTP edge. |

The YouTube adapter follows the current official API constraints: write calls
need OAuth user authority, `videos.insert` supports resumable upload, and
unverified API projects are restricted to private uploads. See the
[YouTube insert reference](https://developers.google.com/youtube/v3/docs/videos/insert),
[resumable upload guide](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol),
and [OAuth web-server guidance](https://developers.google.com/identity/protocols/oauth2/web-server).

## Gate One: Phase 7–8 Release Repairs

### Durable Cost Lifecycle

Replace the creative workflow's in-memory budget comparison with an atomic
PostgreSQL lifecycle:

```text
estimate -> authorize -> reserve -> link creative job/effect -> submit
         -> pending actual or reconcile actual -> settle -> release unused
```

One reservation is identified by the creative effect idempotency identity.
The transaction locks the applicable canonical budget, returns the prior
reservation only when its estimate matches, records estimated and reserved
integer micro-units, and rejects insufficient budget before submission. It
links the reservation to both `creative_jobs` and `external_effects`.

Settlement is idempotent. An absent provider usage value remains explicit as
`pending_actual`; it never becomes zero. Settlement writes one actual entry and
one release entry. If actual usage exceeds the reservation, the overage is
recorded, the effect remains historically complete, and downstream package
advancement fails closed until an operator-approved budget policy resolves it.
Retries and restarts cannot reserve or settle twice.

### Complete Creative Provider Lifecycle

Persist canonical provider states:

```text
planned -> submitting -> submitted -> running -> completed
                                       |-> failed | cancelled | dead_lettered
```

The selected provider's capability metadata declares supported polling,
webhooks, cancellation, timeout, and reconciliation behaviour. Submission
persists the idempotency/effect plan before the remote call. A crash after
acceptance always reconciles by provider request/session/external ID and fails
closed when existence cannot be established.

Webhook receipts are deduplicated by provider, provider delivery identity or
safe payload hash, and external job identity. They retain verified signature
result, safe metadata/hash, trace/provenance links, and their canonical state
projection. A conditional transition makes webhook and polling convergence
order-independent. Cancellation first reconciles the persisted external ID and
only invokes `provider.cancel` when the selected adapter advertises it.

### Capability Selection and Variants

The Production Agent emits a bounded plan. The workflow persists that plan and
uses `CreativeCapabilityRegistry` rather than a workflow-local provider or
`text_to_video` assumption. Candidate filtering considers requested capability,
modality, controls, enablement, contract compatibility, policy, allowed
provider, explicit provider constraint, formats, aspect ratio, duration,
concurrency/rate state, max variants, and available budget.

Each variant has its provider/model/version, canonical request key, technical
validation, cost/reservation link, provenance, and selected/rejected reason.
Unsupported or unavailable capability requests fail before any provider effect.

### Rights and Provenance

The workflow accepts an explicit rights context containing applicable asset
license, consent, likeness, voice, usage-restriction, reference-asset,
territory, channel, commercial-use, expiry, and revocation identities. It
persists canonical associations to assets and production lineage. `asset_provenance`
records origin, ingredients, transformations, provider/model, and actual C2PA
validation state. `not_configured` remains a disclosure fact and never a valid
credential. The final package gate fails closed when configured rights or C2PA
requirements are unsatisfied.

### Immutable Distribution Decisions

Records referenced by an approved ready package are immutable: selected title,
thumbnail, localization, originality evaluation, disclosure decision, platform
profile decision, distribution package, and final approval. Repository writers
insert a new version for a changed decision. Database triggers reject mutation
or deletion of records reachable from a ready package, protecting the invariant
even when a caller bypasses the service layer.

## Gate-One Verification

Tests are written red-first for every seam: insufficient budget before submit;
crash/retry before and after reserve, submit, actual-usage receipt, settlement,
and release; duplicate webhook; poll/webhook race; timeout; cancellation;
provider replacement/unavailability; explicit provider requirement; quota;
rights expiry/revocation/territory/channel/likeness/voice; C2PA required versus
optional; and direct SQL immutability attempts after ready-package approval.

Only after those tests pass, run the documented Phase 7–8 verifier, full
non-live regression suite, compiler, whitespace check, architecture validation,
strong Phase 1–6 regressions, and a fresh independent review. Documentation
must distinguish fixture, live, and `NOT RUN` results accurately.

## Gate Two: Phase 9 Governed Publishing

### Canonical Domain

Add provider-neutral versioned records and contracts for:

```text
PublisherAccount
PublisherConnection
PublisherCapabilityProfile
PublicationRequest
PublicationPlan
PublicationSchedule
PublicationAttempt
PublicationStatusEvent
PublisherWebhookReceipt
RemotePublicationReceipt
Publication
```

`ReadyToPublishPackage`, `PublicationRequest`, `Publication`, and a remote
platform post are separate identities. One ready package may originate multiple
publication requests and platform publications without mutation.

`PublisherAccount` is explicitly workspace-bound. `PublisherConnection` stores
only credential-reference, required/granted scopes, expiration/refresh status,
revocation status, and a connection version. The secure resolver returns an
ephemeral credential lease only at the adapter edge after policy checks.

`PublisherCapabilityProfile` is versioned by platform, API/adapter version,
account type, app/client identity, certification/audit state, scopes,
content/visibility/scheduling/upload/disclosure support, limits, health, and
verification source/date. Selection fails closed when request behaviour exceeds
the stored profile.

### Publisher Contract and Adapters

`PublisherAdapter` exposes only project-owned DTO methods for preflight,
prepare-delivery, create/resume upload, submit, reconcile, status, cancel or
delete where supported, webhook verification, and capability refresh. Optional
methods are advertised through capability metadata; platform SDK values never
cross the contract.

Publisher selection is registry- and capability-driven rather than platform-
specific. YouTube, TikTok, Instagram, LinkedIn, deterministic fixtures, and
future adapters are independently versioned candidates; canonical publication
logic selects a compatible enabled adapter without importing or special-casing
any platform SDK.

The deterministic publisher fixture simulates private account-bound submission,
accepted remote IDs, delayed processing, signed duplicate webhooks, polling,
quota denial, timeout, cancellation, crash-after-acceptance, and ambiguous
reconciliation. It is the complete CI path.

The YouTube adapter is disabled unless a secure credential resolver, explicit
connection, `youtube.upload` grant, current private-only profile, and opt-in
live configuration are present. It uses resumable upload sessions and stores
only the session/remote identifiers and safe receipt metadata. It does not
claim public uploads, audit completion, or live credentials in CI.

### Publication Lifecycle

```text
ready package + explicit account
-> immutable publication request
-> reauthorize exact package/account/profile/policy/rights/disclosure/budget
-> immutable publication plan
-> durable immediate execution or Temporal schedule
-> planned -> authorized -> submitting -> accepted -> processing -> published
                                     |-> failed | cancelled | dead_lettered
                                     |-> ambiguous_requires_reconciliation
-> immutable remote receipt -> canonical publication
```

Immediately before an external write, the deterministic runtime rechecks the
ready-package version, account/workspace, profile, destination, locale,
territory, policy, rights/consent, synthetic disclosure, credential state,
scopes, current publishing approval, rate/quota, and durable budget
reservation. A retry after ambiguity reconciles a request key, upload session,
container, remote ID, or webhook receipt; it never republishes blindly.

For URL-pull platforms, `PublicationDelivery` creates a HTTPS, short-lived,
asset-specific delivery capability with scope/audit metadata. Internal object
storage never becomes public and its administrative credentials never leave the
object-store adapter.

### Scheduling, Webhooks, and Receipts

Temporal schedules reference immutable ready-package and publication-request
versions. A changed account, media, title, locale, schedule, or policy creates
a new versioned request/plan and receives a new authorization; it cannot mutate
the ready package. Publisher webhook receipts use the same verified,
deduplicated, race-safe pattern as creative receipts.

The immutable remote receipt stores platform, adapter/version, account ID,
remote post/media and submission/session IDs, final URL when available,
visibility, actual disclosure projection, processing state, safe metadata hash,
actual cost, and audit/provenance/trace links. `Publication` projects the
canonical final status and history without altering the ready package.

## Phase-9 Verifiers

The deterministic end-to-end verifier proves:

```text
ReadyToPublishPackage@v1
-> explicit account/profile/request
-> policy/scope/rights/disclosure/budget reauthorization
-> durable plan/schedule
-> fixture acceptance
-> hard worker exit
-> replacement-worker reconciliation without duplicate post
-> duplicate webhook plus poll convergence
-> immutable remote receipt
-> Publication
-> reverse lineage to source/evidence and agent/model/tool runs
```

It includes authority, workspace, credential, capability/audit, visibility,
approval/consent, budget, rate/quota, duplicate start/webhook, crash, timeout,
ambiguous state, processing failure, schedule cancellation, and disclosure
mapping failures. The opt-in YouTube smoke test uses only configured private or
test visibility and reports `NOT RUN` without credentials.

## Documentation and Release Status

After verified implementation, update the master README, architecture map and
Archify viewer, API/SDK/CLI guidance, database, workflows, recovery,
scheduling, webhooks, governance, capability matrix, credential model,
dependencies, ADRs, verification, limitations, Phase 9 handoff, and persistent
implementation progress. The final report lists exact commands, `PASS`/`FAIL`/
`NOT RUN` evidence, independent-review result, known limitations, and the
analytics/learning handoff. Analytics ingestion, experimentation, autonomous
learning, browser access-control bypasses, and multi-platform expansion remain
outside Phase 9.
