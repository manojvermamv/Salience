Continue from the currently verified **Phases 1–6 Salience repository**.

Treat the current repository, `docs/core`, architecture docs, ADRs, contracts, migrations, tests, `docs/implementation-progress.md`, and `docs/phase-7-handoff.md` as authoritative context.

Do not redesign or duplicate the Phase 1–6 foundation.

The next objective is to complete **Phase 7 and Phase 8 end-to-end**:

`ContentBrief@v1 -> script/copy -> creative direction -> media production -> edit/compose -> repurpose/localize -> distribution packaging -> verification/governance -> immutable Ready-to-Publish Package`

Do **not** implement live publishing, production social-account writes, analytics optimization, experiments, or autonomous learning in this build. Those begin after this slice.

Use the engineering loop already established by the project:

`Understand -> Spec -> Verifier -> Environment -> Implement -> Verify -> Inspect -> Repair -> Re-verify -> Checkpoint -> Repeat`

Work autonomously and checkpoint each verified coherent slice.

Do not stop for ordinary reversible dependency or implementation choices. Ask the owner only for credentials, destructive host cleanup, paid external effects that cannot safely be fixture/test-mode constrained, rights/consent decisions, or other irreversible production choices.

## Binding Phase-6 handoff

Phase 7 begins from a selected immutable `ContentBrief@v1`.

Do not:

- rebuild the topic;
- re-rank Phase-5 opportunities;
- mutate the selected strategic package;
- silently add unsupported factual claims;
- overwrite evidence or research decisions;
- reconstruct Phase 5–6 reasoning from scratch.

Phase 7 may request **additional evidence** only when content production discovers a specific unresolved claim or creative requirement.

Any such additional research must append new provenance rather than modifying historical Phase-5/6 evidence.

Every Phase-7/8 artifact must retain lineage back to:

`ContentBrief -> selected package -> opportunity -> signal -> evidence/source -> agent/tool/model runs -> content program`

---

## Pre-Phase-7 production readiness gate

Before media implementation, inspect the real host.

The latest verified state reported approximately:

`99% filesystem usage / ~319 MB free`

Do not install large browser binaries, model weights, media packages, or create isolated Docker stacks until storage is assessed.

Measure at least:

- filesystem free space;
- Docker images;
- Docker volumes;
- Docker build cache;
- project artifact usage;
- Temporal/PostgreSQL/Garage usage;
- expected Phase-7 temporary-media requirements.

Do not run broad destructive commands such as global Docker prune without explicit owner authorization.

Prefer:

- project-scoped cleanup;
- streaming provider outputs directly into owned object storage;
- bounded temporary directories;
- deleting verified transient files after canonical artifact persistence;
- avoiding duplicate media copies;
- object-store lifecycle policies;
- remote creative providers when they reduce local compute/storage burden.

Establish configurable storage quotas and a minimum-free-space guard before media work starts.

If safe media verification is impossible because storage is genuinely insufficient, complete all non-media work possible and report the exact storage blocker rather than pretending media verification passed.

---

# Phase 7 — Script, Creative Direction and Media Production

## Script/copy canonical model

Create versioned canonical entities such as:

- `ScriptVersion`;
- `ScriptSection` or equivalent structured script representation;
- `CreativeBrief`;
- `Storyboard`;
- `ShotPlan`;
- `CreativeJob`;
- `ProviderJob`;
- `Asset`;
- `AssetVariant`;
- `AssetRelationship`;
- `CaptionTrack`;
- `Composition`;
- rights/provenance records.

Names may adapt to existing conventions, but preserve the conceptual separation.

A script must be derived from the immutable `ContentBrief`.

Script versions should preserve:

- parent brief ID;
- script version;
- intended platform/format;
- target duration;
- hook;
- body/story structure;
- payoff;
- CTA if allowed;
- claim references;
- evidence references;
- narration/dialogue;
- on-screen text;
- visual cues;
- source/agent/model lineage.

Do not flatten factual claims into untraceable prose.

## Writer Agent

Add or productionize a directly callable `writer_agent`.

It must use the existing canonical agent contract and remain:

- directly callable by an end user;
- callable by the Lead Agent;
- model-independent;
- framework-independent.

It may use the same underlying model as another agent.

The Writer Agent should produce structured script output rather than only free-form text.

It must preserve approved claim/evidence linkage from the brief.

Unsupported new claims must be:

- rejected;
- explicitly marked unverified;
- or sent through a bounded additional-evidence path.

They must never silently enter an approved script.

## Script verification

Before creative production, evaluate:

- brief fidelity;
- claim support;
- hook/promise consistency;
- audience fit;
- brand/style rules;
- length/duration constraints;
- repetition;
- misleading framing;
- factual contradiction;
- CTA policy;
- platform-independent safety rules.

Use deterministic checks where possible.

Semantic evaluators may supplement them.

Maintain separate draft, revised and approved versions rather than overwriting one record.

---

# Creative Director / Storyboard

Create or productionize a directly callable `creative_director_agent`.

Its input is the approved script plus `ContentBrief`.

Its job is to transform meaning into production intent.

Produce a structured creative plan containing, where relevant:

- scene/shot sequence;
- duration per shot;
- narration/dialogue mapping;
- visual description;
- framing/camera direction;
- reference assets;
- character/person identity requirements;
- on-screen text;
- transitions;
- B-roll;
- graphics/charts;
- music/SFX intent;
- avatar/voice requirements;
- aspect ratio;
- resolution;
- target channels;
- generation constraints.

The Creative Director chooses **what is needed**, not a permanently hard-coded provider.

For example:

`shot requires avatar presentation`
-> request `avatar_video`

`shot requires cinematic generated video`
-> request `text_to_video` or `image_to_video`

`shot only needs deterministic captions`
-> normal media tool, not an agent.

---

# Canonical Creative Capability Layer

Treat creative systems as capability providers.

Extend the existing plugin/capability registry rather than creating provider-specific workflow branches.

At minimum support capability vocabulary around:

- `generate_image`;
- `edit_image`;
- `text_to_video`;
- `image_to_video`;
- `reference_guided_video`;
- `first_last_frame_video`;
- `extend_video`;
- `avatar_video`;
- `text_to_speech`;
- `voice_clone`;
- `lip_sync`;
- `dub_video`;
- `translate_video`;
- `music_or_sfx`;
- `caption`;
- `compose_video`;
- `transcode`;
- `thumbnail_render`;
- `creative_analysis`;
- `upscale`.

Providers publish capability metadata rather than becoming domain types.

Provider metadata should include where available:

- provider ID/version;
- model/version;
- input/output modalities;
- formats/codecs;
- aspect ratios;
- resolution;
- duration constraints;
- async support;
- webhook support;
- polling requirements;
- concurrency;
- rate limits;
- estimated latency;
- cost/credit hints;
- test/sandbox capability;
- credential scope;
- policy limitations;
- provider-specific extension schema.

The Lead/Creative/Production agents request capabilities and constraints.

The runtime chooses an allowed implementation.

A provider may still be explicitly requested when the creative intent requires a unique feature.

---

# Provider adoption strategy

Do not implement every vendor.

Research the current official surfaces and select the smallest useful set.

Current candidate directions include:

- Higgsfield API / MCP / CLI;
- HeyGen REST / CLI / Remote MCP;
- Synthesia REST + signed webhooks;
- Google Veo through Gemini/Vertex APIs;
- local/open models where operationally sensible;
- existing open media systems where reuse beats custom development.

All remain adapters.

Prefer the most stable production transport for long-running automated jobs.

Agent-native MCP/CLI paths may also be supported where they improve interactive production.

Do not leak provider request/response classes into canonical entities.

Provide at least:

1. one deterministic fixture creative provider for CI;
2. one real provider adapter that can be enabled with credentials;
3. a second provider or provider fixture proving capability-level replacement.

If credentials are unavailable, implement and test the adapter contract without claiming a successful live generation.

Do not require a paid provider merely for CI.

---

# Long-running creative jobs

Creative generation must use the existing durable workflow substrate.

A media generation request must have a canonical lifecycle such as:

`requested -> authorized -> budget_reserved -> submitted -> running -> completed/failed/cancelled -> artifact_reconciled`

Persist the external provider job ID.

Support:

- webhook completion where available;
- polling fallback;
- restart-safe resume;
- timeout;
- cancellation where supported;
- retry/reconciliation;
- cost reservation;
- actual-cost capture;
- provider failure classification.

Never blindly resubmit an expensive generation after timeout.

First reconcile the provider's external job state.

Signed provider webhooks must be verified when the provider supports signatures.

---

# Production Agent

Add or productionize `production_agent`.

It may manage a long creative loop such as:

`creative brief -> provider selection -> generation variants -> technical validation -> comparison -> refinement -> selected assets`

The Production Agent may delegate to tools/providers but remains bounded by:

- cost budget;
- max variants;
- time budget;
- tool scopes;
- provider scopes;
- asset quota.

Do not allow open-ended “keep generating until good” loops.

Create explicit termination criteria.

---

# Variant generation and selection

Support generating multiple creative variants where useful.

Each variant must retain:

- provider/model;
- prompt/normalized request;
- provider extension fields;
- references;
- seed when available;
- generation time;
- cost;
- external job ID;
- artifact hash;
- technical properties;
- verifier results.

Do not store secrets or raw credential material.

The selected asset must record why it won.

Rejected variants remain lineage-visible where retention policy allows.

---

# Media composition

Prefer proven tools rather than custom media primitives.

Use FFmpeg/ffprobe or an equivalent mature media layer for deterministic work such as:

- transcoding;
- concatenation;
- overlays;
- audio mixing;
- caption burn-in;
- clipping;
- resizing;
- aspect-ratio conversion;
- loudness normalization;
- frame extraction;
- media inspection.

Keep the media engine behind a project-owned contract.

Do not implement codecs, video muxing or subtitle rendering from scratch.

Validate FFmpeg availability/version before depending on it.

---

# Media technical validation

Every produced media artifact should be checked for applicable properties:

- MIME/container;
- codec;
- dimensions;
- aspect ratio;
- duration;
- frame rate;
- audio presence;
- sample rate;
- loudness;
- corruption/decode errors;
- file size;
- caption validity;
- blank/black frames where detectable;
- missing audio;
- expected scene count where appropriate.

Technical validation should be deterministic when possible.

Semantic/vision evaluation may supplement but not replace basic media validation.

---

# Asset ownership and storage

Copy completed assets into canonical owned object storage whenever provider terms permit.

Do not rely indefinitely on provider-temporary URLs.

Persist:

- content hash;
- MIME type;
- dimensions;
- duration;
- origin;
- parent assets;
- provider/model;
- creation parameters;
- generation run;
- rights metadata;
- content-origin metadata;
- storage object reference.

Avoid duplicate bytes through content-hash reuse where appropriate.

---

# Rights, likeness, voice and consent

Phase 7 introduces a materially stronger rights problem than previous phases.

Create canonical support for concepts such as:

- `AssetLicense`;
- `ConsentRecord`;
- `LikenessIdentity`;
- `VoiceIdentity`;
- `UsageRestriction`;
- attribution requirements;
- permitted channels;
- commercial-use permission;
- territory where relevant;
- expiration;
- revocation/takedown state.

Exact names may fit the existing schema style.

An agent must not infer consent merely because it can technically access a face, voice, avatar or reference image.

Generation involving a real person's likeness or cloned voice must pass the configured consent/rights policy.

If the rights state is unknown, fail closed or request human authorization according to policy.

---

# Content provenance / C2PA

The data model already contains future C2PA hooks.

Phase 7 should turn this into an actual provenance boundary.

Research and evaluate current C2PA 2.4-compatible open tooling rather than building cryptographic manifest machinery from scratch.

At minimum, canonical assets should support:

- generated vs captured/imported origin;
- AI/tool/provider identity;
- source ingredients/reference assets;
- transformation chain;
- parent/derived relationships;
- Content Credentials/C2PA manifest reference;
- validation status;
- signer/trust metadata where available.

Prefer generating/verifying real C2PA credentials for at least one supported media path if current open tooling and formats make this practical.

If full credential signing cannot yet be productionized, implement the adapter and canonical provenance model without falsely claiming signed provenance.

---

# Phase 8 — Distribution Packaging and Governance

Phase 8 converts approved content/media into platform-ready packages.

It still does **not publish them**.

## Distribution package

Create a canonical, versioned platform-neutral representation that can produce channel-specific packages.

Include as applicable:

- final title;
- thumbnail;
- caption;
- description;
- keywords;
- hashtags;
- chapters;
- CTA;
- language/locale;
- subtitles;
- alt text;
- target platform;
- target format;
- planned schedule window;
- synthetic-media disclosure state;
- rights/provenance references.

Keep this separate from Phase-6 strategic packaging.

Strategic packaging answers:

`Why and how should this idea be presented?`

Distribution packaging answers:

`How should the completed content appear on this platform?`

---

# Platform profiles

Do not hard-code platform requirements inside prompts.

Create versioned `PlatformProfile` / `PlatformPolicyProfile` or equivalent.

It should carry things such as:

- supported media type;
- aspect ratio;
- duration;
- caption/title limits;
- thumbnail rules;
- metadata support;
- disclosure requirements;
- known API limitations;
- target locale requirements;
- platform-specific validator versions.

Actual publishing authorization/certification belongs to the later publishing phase.

For Phase 8, produce **ready-to-publish canonical packages**, not live posts.

---

# Thumbnail and title generation

Treat titles/thumbnails as first-class measurable artifacts.

Generate multiple candidates where useful.

Evaluate:

- clarity;
- audience relevance;
- promise/content alignment;
- novelty;
- visual readability;
- misleading/clickbait risk;
- duplication against recent content.

Preserve rejected candidates for future analytics where retention policy allows.

Do not allow title/thumbnail promises that the script/media does not deliver.

---

# Repurposing

Support content derivatives without treating a derivative as an unrelated new content item.

Examples:

`long video -> Short`
`video -> carousel`
`video -> thread`
`video -> article`
`one language -> localized edition`

Maintain parent/derived relationships.

A repurposed asset must preserve source claims/evidence and update platform-specific packaging.

Do not simply truncate content without semantic verification.

---

# Localization

Localization must be more than literal translation.

Represent:

- source locale;
- target locale;
- translation version;
- cultural adaptation;
- localized CTA;
- subtitle/audio version;
- translated claims;
- terminology constraints.

Factual claims must remain linked to the original verified claims.

Do not create a new unsupported claim during localization.

---

# Authenticity and anti-spam controls

Before marking content ready to publish, compare it against:

- recent program content;
- recent scripts;
- titles;
- thumbnails;
- visual templates;
- repurposed siblings.

Track useful metrics such as:

- semantic novelty;
- narrative novelty;
- visual/template reuse;
- content overlap;
- creator value added;
- repurpose distance.

Do not optimize for mass output alone.

The system should scale useful/original content, not mechanically flood channels with minor variants.

---

# Synthetic-media disclosure

Create a deterministic disclosure decision record.

The package should know, at minimum:

- whether material is AI-generated or materially altered;
- whether it appears realistic;
- whether it depicts a real person;
- whether likeness/voice cloning was involved;
- applicable platform disclosure requirement;
- applicable jurisdiction/domain requirement;
- C2PA/content-credential status.

Do not leave disclosure solely to the Lead Agent's prose judgement.

The later Publisher will consume this record.

---

# Final governance gate

Before a package becomes `ready_to_publish`, execute both deterministic and semantic verification.

Verify applicable:

- facts/claims;
- evidence links;
- brand/style;
- rights/licenses;
- likeness/voice consent;
- content provenance;
- synthetic-media disclosure;
- technical media validity;
- duplicate/originality;
- misleading claims;
- platform constraints;
- domain policy;
- jurisdiction policy;
- safety;
- budget/cost reconciliation.

Human approval should be requested only when the configured policy requires it.

Approval must not regenerate upstream content.

Approval accepts/rejects a specific immutable version.

---

# Ready-to-Publish Package

The final Phase-8 artifact should be an immutable/versioned object such as:

`ReadyToPublishPackage@v1`

or a project-consistent equivalent.

It should contain or reference:

- content program;
- original `ContentBrief@v1`;
- approved `ScriptVersion`;
- selected media assets;
- derived assets;
- selected title/thumbnail;
- final metadata;
- target platform/profile version;
- locale;
- disclosure decision;
- provenance/C2PA references;
- rights/consent records;
- verifier results;
- policy versions;
- approval state;
- complete agent/model/tool lineage.

This object becomes the **only valid Phase-9 publishing input**.

The future Publisher must not reconstruct creative decisions from arbitrary database rows.

---

# Callable-agent requirements

By the end of this slice, these logical agents should exist where they provide real independent value:

- Lead Content Agent;
- Research Agent;
- Strategy Agent;
- Writer Agent;
- Creative Director Agent;
- Production Agent;
- Verifier Agent.

Do not force a new model deployment for every agent.

All enabled specialists remain callable through the existing framework-neutral API/CLI/SDK contract.

The Lead Agent uses the same invocation contract.

Do not create an agent for deterministic FFmpeg/media-validation/platform-rule work.

---

# Real provider behavior

The build must remain useful without paid credentials.

CI/default verification should use deterministic fixtures and local media fixtures.

Live provider tests must be explicitly opt-in.

When live credentials are available, validate at least one external creative adapter end-to-end.

Prefer providers' documented async/webhook/job mechanisms.

Current research candidates should be reverified immediately before integration because capabilities, model IDs and pricing change.

Do not write tests that depend permanently on preview model IDs.

Model/provider availability belongs in capability metadata.

---

# Cost and resource governance

Media can be much more expensive than text generation.

Before submitting an expensive generation:

1. estimate cost;
2. reserve budget;
3. check workspace/program/run/provider limits;
4. authorize;
5. submit once;
6. reconcile final usage;
7. release unused reservation.

Support limits such as:

- max candidate renders;
- max provider spend;
- max concurrent jobs;
- max storage bytes;
- max run time.

A Production Agent must not exceed these because it decided more variants might be useful.

---

# Failure and restart scenarios

Test at least:

- process restart during external media generation;
- webhook received twice;
- polling after webhook;
- provider timeout after accepted job;
- provider returns malformed response;
- failed render;
- cancelled render;
- cost exceeds reservation;
- download URL expires;
- artifact download interrupted;
- hash mismatch;
- invalid media file;
- unsupported capability;
- provider unavailable;
- provider policy rejection;
- missing consent;
- revoked consent;
- unsupported claim introduced during script;
- contradictory claim discovered;
- distribution package violating platform profile.

External paid generation must never be duplicated simply because a local worker restarted.

---

# Required Phase-7/8 end-to-end verifier

Run a complete deterministic path similar to:

`selected ContentBrief@v1`
`-> Writer Agent`
`-> ScriptVersion`
`-> script verification`
`-> Creative Director`
`-> Storyboard/CreativeBrief`
`-> Production Agent`
`-> creative fixture provider`
`-> several AssetVariants`
`-> technical validation`
`-> selected media`
`-> FFmpeg/local composition fixture where applicable`
`-> captions`
`-> distribution package variants`
`-> title/thumbnail evaluation`
`-> rights/provenance/disclosure checks`
`-> final governance`
`-> ReadyToPublishPackage@v1`

Then prove complete reverse lineage:

`ReadyToPublishPackage`
`-> distribution package`
`-> assets`
`-> creative/provider jobs`
`-> script`
`-> ContentBrief`
`-> package`
`-> opportunity`
`-> signals/evidence/sources`
`-> agent/model/tool runs`

The whole path must remain durable and restart-safe.

---

# Regression requirements

Do not break the already verified Phase 1–6 guarantees.

Re-run the strongest feasible existing suites after each meaningful checkpoint.

Because host storage is constrained, prefer focused tests and existing services until storage is safely recovered.

Do not claim the previous isolated Docker verifier was re-run unless it actually was.

Keep documentation explicit about:

`PASS`
`FAIL`
`NOT RUN`
`reason`

---

# Required documentation updates

Update actual project docs for:

- Phase-7 handoff consumption;
- script model;
- Writer Agent;
- Creative Director;
- Production Agent;
- creative capability registry;
- creative provider adapters;
- async provider lifecycle;
- FFmpeg/media pipeline;
- asset model;
- rights/consent model;
- C2PA/content provenance;
- technical validators;
- distribution packaging;
- localization;
- platform profiles;
- disclosure policy;
- anti-spam/originality checks;
- Ready-to-Publish Package;
- verification;
- storage/resource requirements;
- exact provider/version dependencies;
- known limitations;
- Phase-9 handoff.

Keep README claims strictly aligned with what was actually verified.

---

# Definition of done

Phases 7–8 are complete only when the system can demonstrably execute:

`immutable ContentBrief@v1`
`-> evidence-preserving ScriptVersion`
`-> creative plan`
`-> restart-safe media production`
`-> owned validated assets`
`-> distribution packaging`
`-> governance`
`-> immutable ReadyToPublishPackage`

with:

- no mutation of Phase 5–6 history;
- complete lineage;
- provider-neutral capability contracts;
- directly callable agents;
- deterministic CI without paid credentials;
- at least one real provider adapter;
- budget reservation/reconciliation;
- technical media validation;
- rights/consent enforcement;
- synthetic-media disclosure state;
- content provenance;
- platform profile validation;
- originality/duplicate safeguards;
- restart-safe async generation;
- no live social publishing.

At completion, produce an implementation report containing:

1. architecture actually implemented;
2. migrations/entities added;
3. callable agents added or changed;
4. provider/creative capability decisions;
5. FFmpeg/media decisions;
6. rights/provenance/C2PA implementation status;
7. Phase-7 verifier results;
8. Phase-8 verifier results;
9. complete integrated E2E evidence;
10. resource/storage measurements;
11. exact reproduction commands;
12. tests not run and why;
13. known limitations;
14. the immutable Phase-9 publishing handoff contract.

The goal is not maximum provider count or maximum generated code.

The goal is one reliable production-quality path from an evidence-linked `ContentBrief` to a governed, immutable **ready-to-publish content package**, while preserving Salience's existing durability, trust, provenance, replaceability and operator-control guarantees.