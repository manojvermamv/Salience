# Creative Production and Distribution Packaging Design

**Status:** Approved for planning and implementation on 2026-09-13

## Goal

Extend the verified Phases 1–6 Salience foundation with one governed,
restart-safe dry-run path from an immutable `ContentBrief@v1` to an immutable
`ReadyToPublishPackage@v1`. The path produces evidence-linked scripts,
creative direction, owned media assets, platform-neutral distribution packages,
and final governance records. It does not publish, collect analytics, run
experiments, or implement learning.

## Constraints

- Phase 7 consumes an existing selected `ContentBrief@v1` by identity. It
  never re-ranks its opportunity, mutates its package, rewrites historical
  claim/evidence decisions, or promotes an unsupported claim.
- PostgreSQL remains canonical for identities, state, lineage, policy,
  approval, cost, rights, and verifier results. Object storage owns asset bytes
  where provider terms permit. Temporal remains the replaceable durable runtime.
- All creative integrations use capability contracts and the existing plugin
  registry; provider SDK/request/response classes never enter canonical models.
- Default verification is fixture-only and dry-run. A live provider requires an
  explicit secret reference and policy authorization. Publishing is absent.
- Every agent remains directly callable through the existing native agent API,
  CLI, and SDK, and receives the same typed contract when called by a workflow.
- Media bytes, temporary URLs, credentials, and C2PA signer material are not
  embedded in jobs, canonical JSON, logs, or agent responses.

## Environment Decision

The 2026-09-13 pre-production audit found 4.1 GiB free on the root filesystem,
7.882 GB of Docker images, 907 MB of Docker volumes, 990 MB build cache, and
no `ffmpeg` or `ffprobe` executable. No cleanup or installation occurred.

The implementation adds a configurable minimum-free-space guard and bounded
temporary-media directory before any media command runs. The environment task
will first inspect the package-manager plan and install only the system FFmpeg
package if it preserves the configured guard. If that cannot be proved, fixture
media and FFmpeg-dependent verification report `NOT RUN` with the measured
reason; no completion claim may rely on it. No global Docker prune is allowed.

## Build-versus-Adopt Decisions

| Need | Decision | Evidence, boundary, and exit path |
| --- | --- | --- |
| Deterministic composition and technical inspection | Adopt the host `ffmpeg` and `ffprobe` commands behind `MediaEngine`; do not implement codecs, muxers, filters, or subtitle rendering. | FFmpeg publishes source and directs Linux users to distribution packages; its default license is LGPL-2.1-or-later, while optional GPL components change obligations. The adapter captures executable/version and isolates command construction. Disable the adapter or replace it without changing canonical assets. |
| C2PA/Content Credentials | Add an owned `C2paTool` boundary and canonical provenance status now; make `c2patool` invocation optional. | C2PA 2.4 is current. `contentauth/c2pa-rs` is MIT/Apache-2.0 and requires Rust 1.88+, which is not installed or justified on the constrained host. The first slice records `not_configured` rather than claiming a signed credential. Later signing/verifying uses the same adapter with operator-provided signer references. |
| External creative provider | Implement a credential-gated Synthesia REST adapter using existing `httpx`, plus deterministic fixture providers. | Synthesia documents `POST /v2/videos`, async video retrieval/webhooks, bounded test-video availability, and plan-dependent access/rate limits. The adapter maps only owned DTOs, has mock-contract tests, and performs no live call without an `env://` secret reference. It can be removed without losing jobs/assets. |
| Second capability implementation | Use a deterministic local fixture provider rather than another paid dependency. | It proves generic `text_to_video` capability replacement, webhook/poll reconciliation, malformed/timeout/cancel cases, and creates portable test assets without cost or credentials. |

## Canonical Data Model

Migration `0006_creative_production_distribution` adds only additive Phase 7–8
tables and foreign keys into existing workspace, content-program, job, agent,
artifact, provenance, budget, approval, and `content_brief_versions` records.

1. **Script and creative intent**
   - `script_versions`: immutable revision key/version, parent brief, source
     script version, status (`draft`, `revised`, `approved`, `rejected`), target
     format/duration, structured sections, claim/evidence IDs, and lineage.
   - `creative_briefs`, `storyboards`, and `shot_plans`: immutable or
     versioned production intent linked to an approved script and original
     brief. Shots map narration, on-screen text, visual/camera direction,
     reference assets, target channels, and generation constraints.
2. **Provider execution and assets**
   - `creative_jobs` and `provider_jobs`: canonical request fingerprint,
     requested capability, lifecycle state, selected provider/capability
     version, external job ID, webhook/poll reconciliation state, timeout,
     cancellation, cost reservation/actual cost links, and failure class.
   - `assets`, `asset_variants`, `asset_relationships`, `caption_tracks`, and
     `compositions`: owned object references, hashes, MIME/technical metadata,
     normalized parameters, provider extension, parent/derived graph,
     selection/rejection reasons, and verifier results.
3. **Rights and provenance**
   - `asset_licenses`, `consent_records`, `likeness_identities`,
     `voice_identities`, and `usage_restrictions`: scope, permitted channels,
     commercial/territory/expiration/revocation fields. Unknown or revoked
     likeness/voice consent is a fail-closed state.
   - `asset_provenance`: generated/captured/imported origin, provider/model,
     ingredient assets, transformations, C2PA manifest reference, validation,
     signer/trust state, and a link to the existing provenance record.
4. **Distribution and final governance**
   - `platform_profiles`: immutable/versioned technical and metadata limits,
     disclosure requirements, locale rules, and validator version.
   - `distribution_packages`, `distribution_package_variants`,
     `title_thumbnail_candidates`, `localizations`, `originality_evaluations`,
     and `synthetic_media_disclosures`: final platform presentation separate
     from Phase-6 strategic packaging.
   - `ready_to_publish_packages`: immutable versioned final record referencing
     one approved script, selected assets, platform profile, final metadata,
     disclosure, rights/provenance, verifier results, policy/approval state,
     and complete lineage. It is the sole future Phase-9 publishing input.

Unique constraints use content-program scope and stable request/version keys.
Every provider submission and owned-byte import uses an idempotency key and
content-hash reuse; rejected variants remain lineage-visible subject to the
existing retention policy.

## Owned Contracts and Services

`salience.creative.contracts` provides immutable request/result DTOs for:

- `ScriptDraftRequest@v1` and `ScriptVersion@v1`;
- `CreativeDirectionRequest@v1`, `CreativeBrief@v1`, and `Storyboard@v1`;
- `CreativeCapabilityRequest@v1`, `ProviderJobResult@v1`, and
  `AssetVariant@v1`;
- `MediaInspection@v1`, `AssetSelection@v1`, and `C2paValidation@v1`;
- `DistributionPackage@v1`, `PlatformProfile@v1`,
  `DisclosureDecision@v1`, and `ReadyToPublishPackage@v1`.

`CreativeRepository` owns idempotent PostgreSQL writes and reverse lineage.
`CreativePolicy` composes existing scope, approval, trust, and budget checks
with capability, provider, rights/consent, storage-quota, and disclosure rules.
`CreativeVerifier` performs deterministic claim, duration, repetition,
technical-media, platform, rights, originality, and disclosure checks before
any state can advance.

## Agents and Capability Providers

The existing fixture agent registry gains four framework-neutral manifests:

- `writer_agent`: produces structured draft/revised scripts from a precise
  brief, retaining claim/evidence IDs. Unsupported claims are rejected or
  explicitly marked unresolved; they cannot enter an approved script.
- `creative_director_agent`: turns an approved script plus brief into a
  storyboard/shot plan and capability requests, never a provider-specific plan.
- `production_agent`: selects an allowed capability implementation, generates
  bounded variants, reconciles accepted jobs, runs deterministic validators,
  and records the selection reason. It stops at configured time, cost, asset,
  and variant ceilings.
- `verifier_agent`: supplies structured semantic review only. Deterministic
  validators enforce all encodable hard constraints.

The plugin vocabulary includes `generate_image`, `edit_image`,
`text_to_video`, `image_to_video`, `reference_guided_video`,
`first_last_frame_video`, `extend_video`, `avatar_video`, `text_to_speech`,
`voice_clone`, `lip_sync`, `dub_video`, `translate_video`, `music_or_sfx`,
`caption`, `compose_video`, `transcode`, `thumbnail_render`,
`creative_analysis`, and `upscale`. Provider manifests carry modalities,
formats/codecs, aspect ratio/resolution/duration bounds, asynchronous/webhook
and polling behavior, concurrency, estimated latency/cost, secret scopes,
policy limitations, and provider-specific extension schema.

`FixtureCreativeProvider` implements deterministic async `text_to_video` with
explicit submit/get/cancel/webhook outcomes. `SynthesiaCreativeProvider` maps
the documented REST API onto the same contract, preserves the external ID,
uses a secret reference, and supports authenticated polling/webhook validation
only when configured. A fixture replacement provider establishes
capability-level portability in CI.

## Durable Workflow

`CreativeProductionWorkflow` receives an exact `brief_id`, workspace/program
IDs, idempotency key, dry-run flag, and target-profile references. It performs:

```text
load immutable brief
-> writer draft / deterministic script checks / immutable approval
-> creative direction and capability planning
-> policy, rights, storage, and budget reservation
-> submit once and persist external provider ID
-> wait for webhook or bounded polling; reconcile before any resubmission
-> import/reuse owned bytes and validate media
-> bounded variant selection and composition/captions
-> distribution, localization, title/thumbnail candidates
-> deterministic disclosure/originality/platform/rights final gate
-> immutable ReadyToPublishPackage@v1 or terminal denied/failed/cancelled state
```

Every stage writes a normal job checkpoint, audit event, provenance record,
and trace identity. A provider timeout after acceptance invokes reconciliation,
not blind retry. Duplicate webhooks, webhook-then-poll, interrupted downloads,
hash mismatch, cancellation, malformed output, policy rejection, missing or
revoked consent, unsupported capability, budget excess, and invalid media are
explicit terminal or repairable outcomes. Approval accepts/rejects an exact
immutable version and never regenerates upstream content.

## Media, Storage, Rights, and Disclosure

`MediaEngine` supports bounded streaming import, `ffprobe` JSON inspection, and
deterministic composition only after the environment guard passes. It cleans
only its verified temporary directory after canonical object persistence and
does not duplicate byte payloads. In-memory fixture inspection supports
non-FFmpeg tests; tests requiring the real binary are labelled `NOT RUN` with
the observed prerequisite if unavailable.

The rights gate denies real-person likeness, voice clone, avatar, or restricted
reference use without active consent and matching channel/commercial/territory
restrictions. Disclosure is deterministic from generated/altered/realistic,
likeness/voice, platform-profile, jurisdiction-policy, and C2PA states.

## Public Surface

Add authenticated, scoped control routes, matching CLI commands, and SDK calls
to start/inspect a creative run, retrieve script/storyboard/assets, inspect
creative-job and provider reconciliation state, list platform profiles, and
retrieve final package plus reverse lineage. All start routes default to
dry-run. There is deliberately no publish endpoint, platform account write,
analytics endpoint, experiment, or learning loop.

## Verification Strategy

Tests are written red-first and grouped by coherent slice:

1. DTO/registry/policy tests prove capability resolution, ceilings, no-secret
   payloads, immutable script revisions, claim fidelity, rights fail-closed,
   platform/disclosure/originality rules, and provider-neutral contracts.
2. Migration/repository tests prove canonical IDs, uniqueness/idempotency,
   asset-byte hash reuse, variant lineage, final package immutability, and full
   reverse lineage to Phase-5/6 source evidence.
3. Provider/media contract tests cover fixtures, Synthesia-shaped HTTP mocks,
   duplicate webhooks, webhook/poll reconciliation, timeout after acceptance,
   cancellation, malformed/download/hash failures, and unavailable FFmpeg.
4. Direct/delegated agent/API/CLI/SDK tests prove the four new agents use the
   existing framework-neutral contract and produce the same canonical types.
5. The Phase 7–8 Temporal verifier hard-exits after fixture acceptance, starts
   a replacement worker, proves a single external submission, and completes:

   ```text
   ContentBrief -> Script -> Creative Plan -> several Variants -> selected
   owned Asset -> technical validation -> captions/composition -> distribution
   package -> disclosure/rights/provenance/platform/originality gates
   -> ReadyToPublishPackage -> complete reverse lineage
   ```

The verifier reports `PASS`, `FAIL`, or `NOT RUN` with an exact reason; it does
not report a FFmpeg, live-provider, C2PA signature, or storage test as passed
without direct evidence.

## Documentation and Handoff

Update README, architecture/workflow/database/API/agents/plugins/governance,
deployment/verification/limitations, dependency decisions, Phase-7 handoff,
and create a Phase-9 handoff. The final documentation must state real provider
and media availability separately from fixture verification and identify
`ReadyToPublishPackage@v1` as the only publishing input.
