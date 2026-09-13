Continue from the current Salience repository and attached/current project documentation.

## Owner decisions — binding

**Release posture: A**

Keep the current Phase 7–8 integration as a **non-release development checkpoint** until all documented release blockers are implemented, the full verification ladder passes, and a fresh independent review finds no critical/high findings.

If the Phase 7–8 work is still isolated from `main`, do not merge it before that gate.

If it has already been merged into `main`, do not revert solely because it is incomplete; instead clearly mark `main` as a non-release development checkpoint and do not tag/release/enable live creative or publishing effects until the gate is satisfied.

**Required implementation scope: A**

Complete **all five documented Phase 7–8 blockers**.

Do not reduce the acceptance criteria.

Do not relabel the missing guarantees as an optional Phase 8.1.

They are required Phase 7–8 guarantees because Phase 9 will trust the resulting immutable `ReadyToPublishPackage@v1`.

Use:

`Understand -> Spec -> Verifier -> Environment -> Implement -> Verify -> Inspect -> Repair -> Re-verify -> Independent Review -> Checkpoint`

Do not start Phase 9 until this release gate is green.

---

# Part 1 — Complete the Phase 7–8 release gate

Treat:

`docs/superpowers/plans/2026-09-13-phase-7-8-review-repairs.md`

as the immediate authoritative repair plan.

Preserve all already-completed review repairs.

Implement the remaining five requirements completely.

## 1. Durable creative cost lifecycle

Remove the workflow-local/in-memory budget decision as the authoritative cost guarantee.

Before any paid/external creative submission:

`estimate -> authorize -> reserve durably -> link to creative job/effect -> submit -> reconcile actual usage -> settle -> release unused reservation`

The canonical PostgreSQL transaction must:

- idempotently create/reuse the reservation;
- associate it with the creative job;
- associate it with the external effect;
- persist estimated amount;
- persist reserved amount;
- persist actual amount where known;
- release unused reservation;
- support explicit unknown/pending actual cost;
- prevent double reservation after retry/restart;
- prevent double settlement;
- define behavior when actual usage exceeds reservation;
- preserve audit/provenance/trace lineage.

Test crash/retry at every meaningful reservation/settlement seam.

Do not let provider submission happen when authorization/reservation fails.

## 2. Complete provider lifecycle

Persist canonical lifecycle states such as:

`planned -> submitting -> submitted -> running -> completed`
or
`failed / cancelled / dead_lettered`

Use provider capability metadata rather than assuming all providers support the same lifecycle.

Implement:

- configured provider timeout;
- bounded polling;
- typed/classified provider failures;
- terminal dead-letter behavior;
- persisted external provider ID;
- restart-safe reconciliation;
- duplicate-safe webhook receipt;
- signature verification for providers that support signed webhooks;
- webhook event persistence;
- poll/webhook race safety;
- cancellation reconciliation;
- `provider.cancel` only when supported;
- cancellation outcome persistence.

A webhook arriving twice must not complete or charge twice.

A webhook completing a job while polling is active must result in one canonical completion.

A restart after provider acceptance must never blindly resubmit an ambiguous paid generation.

## 3. Registry-driven capability and Production Agent execution

Remove remaining workflow-level provider/capability assumptions.

Use:

`Creative Director capability request`
`-> Production Agent bounded plan`
`-> CreativeCapabilityRegistry`
`-> eligible provider candidates`
`-> policy/budget/capability filtering`
`-> selected provider adapter`

Do not hard-code `text_to_video`.

Provider selection must consider applicable:

- requested capability;
- modality;
- required controls/features;
- provider enabled state;
- contract compatibility;
- allowed provider policy;
- effect authorization;
- max variants;
- budget;
- formats;
- aspect ratio;
- duration;
- concurrency/rate state;
- explicit provider constraint when requested.

Invoke and record `production_agent`.

Persist every generated variant and:

- selected/rejected status;
- selection reason;
- provider/model/version;
- request identity;
- cost;
- technical validation;
- provenance.

Add explicit tests for:

- primary provider;
- compatible replacement provider;
- provider unavailable;
- unsupported capability;
- explicit provider requirement;
- variant quota.

## 4. Rights and provenance enforcement

Carry explicit canonical rights information through the actual workflow, not only schema hooks.

Support applicable canonical references for:

- asset license;
- consent;
- likeness identity;
- voice identity;
- usage restriction;
- commercial permission;
- territory/channel restriction;
- expiry/revocation;
- reference asset lineage.

Persist their canonical IDs onto resulting assets/production lineage.

Persist `asset_provenance` for produced assets.

Record C2PA status accurately.

`not_configured`
means exactly that.

It must never be converted into:

`valid`
`verified`
or any equivalent proof.

Final package creation must fail closed when configured rights/provenance requirements are unsatisfied.

Test:

- missing consent;
- revoked consent;
- expired rights;
- disallowed territory/channel;
- real-person likeness without permission;
- voice-clone without permission;
- required C2PA unavailable;
- optional C2PA unavailable;
- valid rights/provenance path.

## 5. Immutable post-approval distribution decisions

Once a `ReadyToPublishPackage@v1` references distribution decisions, none of those records may be mutated in place.

This applies to at least:

- selected title;
- thumbnail;
- localization;
- originality decision;
- synthetic-media disclosure;
- distribution package/version;
- platform-profile decision;
- final approval.

Any change after approval must create:

`new immutable version -> re-run applicable validators -> new final approval -> new ReadyToPublishPackage version`

Do not use an upsert that changes a decision already referenced by an approved ready package.

Add database and repository-level tests proving immutability, not only service-level tests.

---

# Phase 7–8 release re-verification

After all five repairs are complete, run the exact documented verification ladder:

```bash
bash tests/scripts/test_phase_7_8_documentation.sh
bash scripts/verify-phases-7-8.sh
pytest tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q
python -m compileall -q src
git diff --check
node .agents/skills/archify/bin/archify.mjs validate architecture \
  docs/salience-phase-1-8-final.architecture.json \
  --quality showcase --repo-root . --json
```

Also re-run the strongest applicable Phase 1–6 regressions.

Do not change a required failure into a skipped test to get green results.

Then request/perform a fresh independent review.

Phase 7–8 becomes complete/release-ready only when:

- all commands pass;
- no required verifier is falsely marked PASS when actually NOT RUN;
- independent review finds no critical/high issue;
- documentation accurately describes what is live, fixture-only and unavailable.

Only then merge/tag/update release status as appropriate.

---

# Part 2 — Phase 9: Governed Publishing End-to-End

After Phase 7–8 is genuinely complete, begin Phase 9.

The only valid input is:

`ReadyToPublishPackage@v1`

Do not rebuild or mutate:

- research;
- evidence;
- strategic package;
- script;
- media;
- distribution decision;
- approval.

Publishing is a **new external-effect authority boundary**.

Creative approval does not grant publication authority.

The Phase-9 goal is:

`ReadyToPublishPackage@v1`
`-> publication request`
`-> platform/account capability resolution`
`-> credential + authority binding`
`-> platform preflight`
`-> schedule`
`-> re-authorization`
`-> idempotent remote submission`
`-> processing/reconciliation`
`-> immutable remote receipt`
`-> canonical Publication`

Do not implement autonomous learning in this phase.

---

# Canonical Phase-9 domain

Introduce provider-neutral versioned concepts equivalent to:

- `PublisherAccount`;
- `PublisherConnection`;
- `PublisherCapabilityProfile`;
- `PublicationRequest`;
- `PublicationPlan`;
- `PublicationSchedule`;
- `PublicationAttempt`;
- `PublicationStatusEvent`;
- `PublisherWebhookReceipt`;
- `RemotePublicationReceipt`;
- `Publication`.

Names may adapt to existing repository conventions.

Keep these identities separate:

`ReadyToPublishPackage`
≠
`PublicationRequest`
≠
`Publication`
≠
`Remote platform post`

A ready package can potentially result in multiple platform publications without mutating the package.

---

# Account and credential boundary

Create an explicit platform-account binding.

A publisher request must identify the intended account/channel explicitly.

Never infer it from:

- workspace;
- previous publication;
- provider credential;
- creative approval;
- ready-package metadata.

Store credential references only.

Never persist OAuth access tokens, refresh tokens, client secrets, cookies or API secrets in canonical publication records.

Enforce:

- account ownership/binding;
- required scopes;
- credential status;
- expiry/refresh state;
- least privilege;
- workspace/tenant boundary;
- revocation.

If a secure live credential resolver is unavailable, keep the real adapter disabled rather than weakening the secret boundary.

---

# Publisher capability and certification profile

Do not model a platform as simply:

`supports_publish = true`

Publishing capabilities are account-, app-, audit- and permission-dependent.

Maintain a live/versioned capability profile containing applicable:

- platform;
- API/version;
- account type;
- app/client identity;
- required scopes;
- granted scopes;
- audit/review/certification state;
- allowed content types;
- allowed visibility;
- scheduling support;
- upload method;
- resumable upload support;
- URL-pull support;
- verified-domain requirement;
- title/caption limits;
- synthetic-media disclosure support;
- processing-status mechanism;
- webhook support;
- rate limits;
- daily/account/client caps;
- current health.

Examples that the implementation must re-verify from current official documentation before encoding:

- YouTube unverified API projects may upload only with restricted/private visibility until API audit;
- TikTok Direct Post requires `video.publish`, current Creator Info, user authorization/consent, and unaudited clients are restricted to private visibility;
- Instagram publishing is restricted to supported professional-account/API permission combinations and uses its own media-container/publish flow;
- LinkedIn Community Management publishing access requires reviewed/approved API access.

These are examples, not permanent hard-coded assumptions.

Store capability/certification facts as versioned platform metadata.

A platform adapter must fail closed when requested visibility or content behavior exceeds the currently verified capability profile.

Browser automation must never be used to bypass platform API audit, certification, access, consent or scope restrictions.

---

# Publisher adapter contract

Create a project-owned `PublisherAdapter` contract.

Support applicable operations such as:

- preflight;
- prepare media;
- create/upload;
- resume upload;
- submit;
- reconcile;
- status;
- cancel/delete where permitted;
- refresh capability/account state.

Do not require every platform to implement every operation.

Provider/platform-specific SDK objects must not escape the adapter.

The registry selects an adapter based on canonical publication requirements.

Start fixture-first.

Then implement at least one real official-platform adapter behind explicit credentials/configuration.

Prefer the first real adapter that can be safely verified using private/test visibility without public posting.

Do not require live credentials for CI.

---

# Media delivery boundary

Different platforms ingest media differently.

Support canonical delivery modes such as:

- direct file upload;
- resumable upload;
- platform upload session;
- platform container creation;
- pull-from-URL.

Do not make internal object storage permanently public.

If a platform requires a public/verified media URL, create a bounded publication-delivery mechanism with:

- HTTPS;
- limited lifetime;
- exact asset authorization;
- controlled content type;
- no unrelated object access;
- audit trail;
- provider/domain compatibility.

Do not leak object-store administrative credentials.

---

# Publication authorization

Immediately before any external write, re-authorize:

- exact ready-package version;
- account;
- platform capability profile;
- destination;
- locale;
- territory;
- current policy version;
- current rights/consent;
- current synthetic-media disclosure requirement;
- scope;
- credential state;
- publishing approval/consent;
- remaining budget;
- rate/quota state.

Creative approval is insufficient.

Where a platform requires explicit creator/user consent or preview before sending content, represent and verify a durable consent/preview receipt rather than pretending system autonomy overrides the platform rule.

---

# Durable publication cost lifecycle

Use the same corrected durable reservation/settlement architecture established for creative generation.

Before a chargeable publication effect:

`estimate -> reserve -> authorize -> execute -> reconcile -> settle -> release`

Prevent double reservation and double settlement across retry/restart.

---

# Idempotent external publication effect

Before contacting the platform:

persist an immutable/idempotent external-effect plan.

Use a stable request/idempotency identity.

The local lifecycle should make ambiguity explicit, for example:

`planned`
`-> authorized`
`-> submitting`
`-> accepted`
`-> processing`
`-> published`

or terminal:

`failed`
`cancelled`
`dead_lettered`
`ambiguous_requires_reconciliation`

If a process crashes after remote acceptance but before the receipt is durably stored:

**do not blindly republish.**

Use the adapter's strongest recovery method:

- request key;
- resumable session;
- container ID;
- publish ID;
- remote lookup;
- webhook receipt;
- provider reconciliation.

If existence cannot be safely determined, fail closed for operator reconciliation instead of risking duplicate public content.

---

# Scheduling

Use the existing durable Temporal/scheduling substrate.

A scheduled publication must survive:

- API restart;
- worker restart;
- scheduler restart;
- delayed execution;
- temporary provider outage.

Scheduling must reference immutable:

`ReadyToPublishPackage version + PublisherRequest version`

Changing title/media/account/time/policy after scheduling must create/update an explicitly versioned publication plan according to policy.

Do not mutate the underlying ready package.

---

# Webhooks and remote status

Support signed webhook ingestion where available.

Webhook handling must:

- authenticate/verify the provider;
- deduplicate delivery;
- store raw-safe metadata/hash rather than secrets;
- map remote state into canonical status;
- coexist safely with polling;
- preserve trace/provenance;
- not complete one publication twice.

Polling and webhook arrival order must not matter.

---

# Remote receipt

Persist an immutable remote receipt containing applicable:

- platform;
- adapter/version;
- account canonical ID;
- remote post/media ID;
- submission ID/session/container ID;
- final URL if available;
- visibility;
- publication time;
- processing state;
- disclosure state actually sent;
- remote metadata hash;
- cost;
- audit/provenance/trace links.

Never modify `ReadyToPublishPackage@v1` with remote state.

---

# Synthetic-media disclosure

Carry the Phase-8 disclosure decision through the publisher adapter.

Map it to the platform's actual supported disclosure mechanism where available.

For example, if a platform has an explicit synthetic-media field, the adapter should populate it from the governed disclosure record.

If a required disclosure cannot be represented by the chosen platform/API path, fail according to policy rather than silently omitting it.

---

# Publisher policy: no API bypass

Do not fall back to Playwright/Appium merely because the official API denies:

- scope;
- audit;
- account type;
- review;
- rate limit;
- publishing visibility;
- user-consent requirement.

Browser/app posting may only exist as a separately reviewed integration when the platform permits that method and the product policy explicitly enables it.

It is never an access-control bypass.

---

# Publication Agent

Add a callable `publisher_agent` only if semantic coordination provides real value.

Its responsibilities may include:

- selecting an eligible publisher capability;
- explaining why an account/platform cannot currently publish;
- preparing the canonical PublicationRequest;
- monitoring complex multi-step publication jobs.

It does not receive raw credentials.

The deterministic runtime still performs authorization, idempotency, credential resolution and remote writes.

Do not turn each platform API into an agent.

---

# Phase-9 fixture verifier

Build a deterministic publisher fixture with realistic asynchronous behavior.

The fixture must support enough lifecycle to test:

- submission;
- accepted remote ID;
- delayed processing;
- webhook completion;
- polling completion;
- duplicate webhook;
- timeout;
- cancellation where configured;
- crash after acceptance;
- restart reconciliation;
- quota denial;
- capability/audit restriction;
- synthetic-media disclosure propagation.

No public account or paid API may be required.

---

# Phase-9 real-adapter smoke test

Add one opt-in live integration test for the first official platform adapter.

It must not run in normal CI.

Prefer:

- private/test visibility;
- non-public destination;
- or platform-supported draft/testing mode.

The test must report exactly:

`PASS`
`FAIL`
or
`NOT RUN: <reason>`

Never call a missing-credential test PASS.

---

# Phase-9 end-to-end verifier

Prove:

`ReadyToPublishPackage@v1`
`-> explicit account binding`
`-> PublisherCapabilityProfile`
`-> PublicationRequest@v1`
`-> policy/scope/rights/disclosure/budget reauthorization`
`-> durable schedule or immediate plan`
`-> idempotent external-effect record`
`-> fixture publisher`
`-> remote acceptance`
`-> worker hard-exit`
`-> replacement worker`
`-> reconciliation without duplicate post`
`-> webhook/poll convergence`
`-> immutable RemotePublicationReceipt`
`-> canonical Publication`

Reverse lineage must reach:

`Publication`
`-> ReadyToPublishPackage`
`-> distribution decision`
`-> assets`
`-> creative/provider jobs`
`-> script`
`-> ContentBrief`
`-> evidence/source`
`-> agent/model/tool runs`

Test at least:

- missing publishing authority;
- wrong account/workspace;
- revoked credential;
- unsupported platform capability;
- app/audit restriction;
- public visibility disallowed;
- expired approval/consent if policy requires recheck;
- budget denial;
- rate/quota denial;
- duplicate start;
- duplicate webhook;
- crash after acceptance;
- network timeout;
- ambiguous remote state;
- processing failure;
- schedule cancellation;
- disclosure mapping failure.

---

# Minimal post-publication state — not learning yet

Persist enough publication state for the next analytics phase:

- publication ID;
- remote platform ID;
- account/channel;
- published timestamp;
- package/version;
- final platform metadata;
- status history.

Do not yet build autonomous strategy learning or prompt self-modification.

Do not silently scrape metrics using browser automation.

The next phase after publishing will establish canonical analytics ingestion and attribution.

---

# Documentation and architecture truthfulness

Update:

- master README;
- Phase-9 handoff status;
- architecture;
- API;
- SDK/CLI;
- publication lifecycle;
- provider capability matrix;
- credential/account model;
- recovery;
- scheduling;
- webhooks;
- verification;
- limitations;
- dependency/ADR records.

Do not claim:

- public platform publishing verified when only fixture/private testing ran;
- a platform audit completed when it has not;
- account scopes the app does not possess;
- analytics implemented when they are not;
- autonomous learning implemented when it is not.

---

# Phase-9 definition of done

Phase 9 is complete only when Salience can safely execute:

`immutable ReadyToPublishPackage`
`-> newly authorized publication request`
`-> exact account`
`-> capability/certification check`
`-> durable schedule`
`-> idempotent official-platform submission`
`-> processing/reconciliation`
`-> immutable remote receipt`
`-> canonical Publication`

with:

- no mutation of the creative package;
- no implicit account selection;
- no raw credentials in canonical records;
- least-privilege scopes;
- durable publication budget;
- restart safety;
- duplicate prevention;
- poll/webhook convergence;
- explicit platform audit/certification state;
- synthetic-media disclosure propagation;
- fixture-first deterministic CI;
- one real official-platform adapter;
- no access-control/browser bypass;
- complete reverse lineage.

At completion, produce a report covering:

1. Phase 7–8 blocker repairs;
2. full Phase 7–8 re-verification;
3. independent review result;
4. publisher architecture;
5. platform capability/certification model;
6. account/credential boundary;
7. migrations/entities;
8. adapters implemented;
9. scheduling/recovery lifecycle;
10. fixture verifier evidence;
11. opt-in real-adapter status;
12. tests `PASS / FAIL / NOT RUN`;
13. exact reproduction commands;
14. known limitations;
15. precise analytics/learning handoff.

Do not start Phase 9 before the five Phase 7–8 blockers and independent review gate are green.

Do not start autonomous learning in Phase 9.

The goal is not “post to many platforms.”

The goal is one **governed, auditable, restart-safe, duplicate-safe publishing substrate** that can safely scale to many official platform adapters without changing Salience's core identities or trust model.