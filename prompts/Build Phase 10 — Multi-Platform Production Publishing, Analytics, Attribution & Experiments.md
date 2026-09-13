Continue from the current verified Salience Phase 1–9 repository.

Treat the current repository, `docs/core`, current ADRs/specs/plans/reviews, `docs/implementation-progress.md`, `docs/verification.md`, `docs/limitations.md`, and the Phase 1–9 architecture as authoritative implementation context.

Do not redesign or duplicate the existing foundation.

The next objective is to complete the missing real-world loop between publication and measured outcomes:

`ReadyToPublishPackage`
`-> replaceable publisher registry`
`-> real platform delivery`
`-> canonical Publication`
`-> platform analytics`
`-> normalized metric snapshots`
`-> complete content attribution`
`-> governed experiment`
`-> experiment result`
`-> LearningProposal`

Do **not** yet let the system autonomously rewrite active strategy, prompts, agents, routing, policies, or publishing behavior.

Learning remains proposal-only until the next phase.

Use:

`Understand -> Spec -> Verifier -> Environment -> Implement -> Verify -> Inspect -> Repair -> Re-verify -> Checkpoint -> Repeat`

## Release truthfulness gate

Preserve the current Phase-9 status accurately.

The executable Phase-9 gate is green, but a fresh external independent reviewer was unavailable because the reviewer service hit its usage limit.

Do not rewrite that as independent approval.

This limitation does not need to block isolated Phase-10 development, but **live production release remains gated** until either:

- an independent review successfully clears the boundary; or
- the owner explicitly accepts another documented release-review process.

Never silently convert a self-audit into independent review evidence.

---

# Part 1 — Finish real production publishing

The existing publisher architecture is correct:

`canonical publication workflow`
`-> PublisherRegistry`
`-> PublisherAdapter`

Preserve it.

Do not create platform-specific branches inside canonical publication logic.

All platforms remain independently installable, replaceable adapters.

## PublisherRegistry

Expand the existing registry so provider selection is based on capability/profile rather than provider names embedded in workflows.

Each publisher manifest/profile should support applicable facts such as:

- publisher ID;
- adapter/version;
- platform;
- supported content types;
- file-upload support;
- URL-pull support;
- resumable-upload support;
- draft/private/public visibility;
- scheduling support;
- caption/title constraints;
- aspect ratios;
- duration/file-size limits;
- AI/synthetic-media disclosure support;
- webhook/status support;
- deletion/cancellation support;
- account requirements;
- required scopes;
- application/audit/review status;
- credential reference;
- current availability;
- rate/quota state;
- analytics capabilities.

A platform account and an adapter are not the same identity.

One adapter may serve multiple separately governed accounts.

---

# YouTube production enablement

Complete the existing YouTube adapter rather than replacing it.

The current adapter already has the durable private resumable-session boundary.

Add the missing real transfer lifecycle:

`create resumable session`
`-> persist edge session`
`-> stream owned media bytes`
`-> resume interrupted transfer`
`-> finalize`
`-> poll/reconcile processing`
`-> immutable remote video receipt`

Never store bearer credentials or opaque resumable session URLs in ordinary canonical domain rows.

Keep those in the existing secure/durable edge boundary.

Support crash recovery during upload.

Test:

- crash before first byte;
- crash mid-upload;
- expired resumable session;
- duplicate completion;
- credential expiry;
- quota failure;
- processing failure;
- already-uploaded reconciliation.

Remain private-only unless the capability/audit profile explicitly proves broader visibility is authorized.

---

# Instagram publisher adapter

Add an independently registered Instagram adapter using the current official Instagram API surface.

Support professional-account eligibility and current permission/capability requirements.

Use the platform's container/upload/publish lifecycle behind the owned adapter.

Treat:

`media/container creation`
and
`media publication`

as distinct recoverable external effects where the API does so.

Persist remote IDs and reconcile before creating duplicates.

Do not expose provider SDK/API objects through canonical publication records.

---

# LinkedIn publisher adapter

Add an independently registered LinkedIn adapter where current Community Management access permits it.

Use current versioned API headers and permission/capability state.

Support applicable:

- text;
- image;
- video;
- document;
- other currently approved organic types.

Media upload and Post creation remain separate provider effects where required.

Community Management/app-access state belongs to the capability profile.

A missing review/access grant produces an explicit unavailable capability rather than a browser workaround.

---

# Future publisher adapters

The architecture must make future providers independently addable without changes to canonical publication logic.

Adding a provider should normally require:

- adapter implementation;
- capability manifest/profile;
- credential/account binding;
- contract tests;
- provider-specific recovery tests;
- optional live smoke configuration.

It must not require schema redesign or new branches in the canonical publication workflow.

---

# Provider replacement verifier

Prove the canonical publication workflow can run unchanged with at least:

- deterministic fixture publisher;
- YouTube adapter;
- Instagram adapter;
- LinkedIn adapter where access contracts can be implemented;
- replacement fixture demonstrating future-provider substitution.

Normal CI does not require real credentials.

Use deterministic mocked HTTP transports/provider fixtures for contract verification.

Live tests are always opt-in.

Replacing one adapter must not change:

- `ReadyToPublishPackage`;
- `PublicationRequest`;
- `PublicationPlan`;
- `Publication`;
- cost records;
- provenance;
- audit;
- experiment/analytics identity.

---

# No browser publishing bypass

The governed browser capability is not a fallback for rejected API access.

Do not use Playwright/Appium to circumvent:

- missing API approval;
- missing application audit;
- missing account eligibility;
- restricted visibility;
- missing scopes;
- rate limits;
- creator-consent requirements.

Browser/app publishing requires its own future explicit reviewed adapter if a platform permits it.

It is never an access-control bypass.

---

# Part 2 — Canonical analytics system

Build analytics as another provider-neutral capability family.

Do not attach analytics implementation directly to publisher classes.

Use:

`AnalyticsRegistry`
`-> AnalyticsAdapter`

A publishing adapter and analytics adapter may share a platform/account connection, but they remain separate contracts and permissions.

Example:

`YouTubePublisherAdapter`
≠
`YouTubeAnalyticsAdapter`

This allows publishing or analytics providers to be replaced independently.

---

# Canonical analytics entities

Create versioned canonical concepts equivalent to:

- `MetricDefinition`;
- `MetricObservation`;
- `MetricSnapshot`;
- `MetricSeries`;
- `AnalyticsConnection`;
- `AnalyticsCapabilityProfile`;
- `AnalyticsIngestionRun`;
- `AnalyticsCursor`;
- `MetricWindow`;
- `OutcomeAttribution`;
- `AnalyticsQualityRecord`.

Fit exact names to repository conventions.

Every observation should preserve:

- provider;
- account;
- publication;
- remote content ID;
- source API/version;
- metric key;
- canonical semantic if mapped;
- raw provider metric name;
- value;
- unit;
- aggregation type;
- cumulative vs interval semantics;
- dimensions;
- coverage start/end;
- observed time;
- provider update time where available;
- freshness;
- completeness/finality;
- provenance;
- ingestion run;
- trace ID.

Do not overwrite previous snapshots.

Metrics are temporal evidence.

---

# Preserve raw metric semantics

Do not pretend metrics from different platforms are automatically equivalent.

Retain raw provider metric definitions.

Create optional canonical semantic classes only where an explicit reviewed mapping exists.

Examples:

`view`
`impression`
`reach`
`engagement`
`watch_time`
`average_watch_duration`
`like/reaction`
`comment`
`share/reshare`
`save`
`click`
`subscriber/follower_gain`
`conversion`
`revenue`

A canonical metric should carry its mapping/version.

Never destroy the provider-specific observation.

---

# Multi-horizon metric snapshots

Collect performance at configurable horizons such as:

- early;
- 1 hour where meaningful;
- 6 hours;
- 24 hours;
- 72 hours;
- 7 days;
- 30 days;
- lifetime/final where available.

These are requested observation targets, not assumptions that every provider updates on the same schedule.

Store actual collection time and actual coverage.

Use Temporal schedules/timers.

Metric collection must survive restarts.

One missed observation window should be backfillable.

Do not fabricate zero for unavailable metrics.

Use explicit:

`missing`
`unsupported`
`not_authorized`
`not_yet_available`
`deleted`
`private`
`provider_error`

states.

---

# YouTube analytics

Add an official YouTube Analytics adapter.

Use read-only authorization separated from upload authority.

Support applicable metrics/dimensions currently available through YouTube Analytics/Reporting APIs.

At minimum provide a representative content-performance set such as:

- views;
- engaged views where applicable;
- watch time;
- average view duration;
- likes;
- comments;
- shares;
- subscribers gained/lost;
- estimated revenue only when actually authorized.

Preserve supported dimensions such as:

- video;
- date;
- country;
- traffic source;
- device;
- other current permitted dimensions.

Do not request sensitive or unavailable dimensions automatically.

---

# Instagram analytics

Add the provider boundary for Instagram Insights.

Reverify the current official metric set and permission model at implementation time.

Never copy stale metric names from older API versions.

Version the metric capability profile because provider insight metrics can evolve.

---

# LinkedIn analytics

Add an independently replaceable LinkedIn analytics adapter.

Support only currently authorized metrics/scopes.

Version the required API revision.

Do not treat impressions, reach, reactions, clicks, comments, saves, or reshares as interchangeable.

---

# Future analytics adapters

New analytics providers must attach through `AnalyticsRegistry` and `AnalyticsAdapter`.

The canonical analytics data model must not require schema redesign whenever a new provider is added.

Provider-specific metrics remain preserved through raw metric identity plus optional reviewed canonical mappings.

---

# Analytics ingestion idempotency

A retry must not create duplicate observations.

Use a stable identity derived from applicable:

`provider`
`remote content ID`
`metric definition/version`
`dimension set`
`coverage window`
`observation/provider timestamp`

Handle revised provider values.

When a provider legitimately updates historical data, record a new revision rather than rewriting the old observation.

Support:

- counter increase;
- corrections;
- delayed finalization;
- deleted content;
- privacy changes;
- provider metric deprecation.

---

# Analytics privacy and retention

Metrics may include demographic/location information.

Reuse existing Salience classification/retention hooks.

Do not automatically persist unnecessary person-level data.

Prefer aggregated platform analytics.

Apply workspace/account isolation.

Credential access stays least privilege.

---

# Part 3 — Deterministic outcome attribution

Build the content-performance lineage that the original architecture requires.

A metric snapshot must be traceable to:

`Publication`
`-> PublisherAccount`
`-> ReadyToPublishPackage`
`-> distribution package`
`-> title/thumbnail`
`-> localization`
`-> assets`
`-> provider/model creative jobs`
`-> ScriptVersion`
`-> ContentBrief`
`-> strategic package`
`-> topic/opportunity`
`-> source signals/evidence`
`-> strategy version`
`-> Lead/Specialist Agent versions`
`-> model/tool versions`
`-> publication timing`

Create canonical `OutcomeAttribution` records rather than recomputing historical attribution from today's mutable configuration.

This attribution layer is what future learning consumes.

---

# Cost and business outcome attribution

Join existing cost ledgers with performance.

Support derived measures where definitions are explicit:

- total production cost;
- publication cost;
- analytics cost;
- cost per 1k views/impressions;
- cost per engagement;
- cost per conversion;
- revenue;
- estimated contribution/margin.

Never divide unavailable metrics into fake zero-cost values.

Store derived metric formulas/version.

---

# Conversion events

Introduce an optional provider-neutral external conversion boundary.

Examples:

- website conversion;
- lead;
- signup;
- purchase;
- newsletter subscription.

Use explicit attribution identifiers such as campaign/publication/link IDs where available.

Do not implement invasive identity tracking.

A conversion must preserve its actual attribution certainty:

`direct`
`platform_reported`
`first_party`
`modeled`
`unknown`

Do not convert correlation into deterministic causation.

---

# Part 4 — Experiment system

Now implement the existing core architecture's experiment model.

An experiment never silently changes production behavior.

Create concepts equivalent to:

- `ExperimentDefinition`;
- `ExperimentVersion`;
- `ExperimentVariant`;
- `ExperimentAssignment`;
- `ExperimentExposure`;
- `ExperimentObservation`;
- `ExperimentAnalysis`;
- `ExperimentDecision`.

---

# Experiment contract

Every experiment must predefine:

- hypothesis;
- treatment;
- control/baseline;
- eligible population;
- randomization/assignment unit;
- assignment method;
- primary metric;
- secondary metrics;
- guardrails;
- observation window;
- delayed-metric window;
- minimum sample/time;
- minimum effect of interest where applicable;
- stopping rule;
- sequential-analysis policy;
- multiple-testing policy;
- keep/rollback rule.

No metric shopping after seeing results.

Changes to these fields create a new experiment version.

---

# Experiment modes

Support explicit modes rather than pretending every social experiment is randomized:

`randomized`
`platform_native`
`time_split`
`matched_control`
`observational`

Potential future:

`bandit`

Do not implement autonomous bandit optimization in this phase unless objective verification proves it is necessary.

An observational comparison must never be labeled a causal A/B result.

Platform algorithms, audience composition, topic choice and publication time are confounders.

Record them where possible.

---

# Experiment assignment

Experiments may compare versioned choices such as:

- strategic package;
- hook;
- script;
- creative provider/model;
- thumbnail;
- title;
- format;
- duration;
- posting time;
- platform-specific packaging.

Never modify an already published item merely to create a variant.

Each treatment remains a normal versioned Salience artifact/publication.

---

# Statistical engine

Before adding a statistical dependency, evaluate maintained open-source options.

Use deterministic statistics code/library for quantitative analysis rather than asking an LLM whether a variant "won."

LLMs may interpret results, not calculate authoritative significance.

Support confidence/uncertainty and insufficient-data outcomes.

A valid result may be:

`winner`
`no_detectable_difference`
`guardrail_failure`
`inconclusive`
`invalid_experiment`

Do not force every experiment to produce a winner.

---

# Growth Analyst Agent

Add a directly callable `growth_analyst_agent` if its independent semantic value is demonstrated.

It receives canonical metrics, attribution and experiment results.

It may:

- summarize performance;
- identify patterns;
- compare cohorts;
- explain possible causes;
- suggest follow-up experiments;
- create `LearningProposal` objects.

It must not directly modify active:

- strategy;
- prompt;
- model routing;
- agent manifest;
- platform policy;
- budget;
- publishing schedule.

Its output is advisory/proposal-level only.

---

# LearningProposal handoff

Introduce a versioned `LearningProposal@v1` or equivalent.

It should contain:

- observed evidence;
- attribution references;
- experiment references;
- proposed behavior change;
- expected benefit;
- affected components;
- risks;
- guardrails;
- confidence;
- required next experiment;
- rollback target.

This becomes the only valid input to the next autonomous-learning phase.

Do not implement silent self-rewriting now.

---

# Analytics memory

Activate the existing analytics-memory concept.

Write only canonical derived knowledge with explicit provenance.

Store things such as:

- experiment outcomes;
- stable performance observations;
- validated audience/content hypotheses.

Do not turn every temporary metric movement into durable semantic memory.

Attach:

- source metric IDs;
- experiment IDs;
- confidence;
- validity horizon;
- writer actor;
- supersession/conflict information.

---

# Observability

Continue using OpenTelemetry-compatible run correlation.

Trace:

`analytics schedule`
`-> provider request`
`-> raw metric ingest`
`-> canonical observation`
`-> attribution`
`-> experiment analysis`
`-> LearningProposal`

Correlate with:

- workspace;
- program;
- publication;
- experiment;
- analytics run;
- provider account.

Do not put credentials or sensitive raw demographic payloads into traces.

---

# Phase-10 deterministic fixture environment

Create deterministic publisher and analytics fixtures capable of simulating:

- publication success;
- publication processing delay;
- publication failure;
- provider replacement;
- metric growth;
- delayed metrics;
- revised historical value;
- unsupported metric;
- provider failure;
- rate limit;
- deleted content;
- counter correction.

Create at least one synthetic experiment dataset with known expected analysis.

CI must not require live social credentials.

---

# Phase-10 end-to-end verifier

Prove:

`ReadyToPublishPackage`
`-> PublisherRegistry`
`-> selected replaceable PublisherAdapter`
`-> canonical Publication`
`-> analytics schedule`
`-> AnalyticsRegistry`
`-> provider analytics adapter`
`-> raw observation`
`-> canonical MetricSnapshot`
`-> restart`
`-> idempotent continuation`
`-> second observation window`
`-> OutcomeAttribution`
`-> experiment assignment`
`-> experiment observations`
`-> deterministic analysis`
`-> ExperimentDecision`
`-> Growth Analyst interpretation`
`-> immutable LearningProposal`

Reverse lineage from the final LearningProposal must reach the original:

`niche`
`-> research/evidence`
`-> topic`
`-> strategic package`
`-> ContentBrief`
`-> script`
`-> creative assets`
`-> distribution package`
`-> Publication`
`-> metric observations`
`-> experiment`

---

# Required failure tests

Cover at least:

- unsupported publisher capability;
- replacement publisher selection;
- publisher credential missing;
- publisher account mismatch;
- publication restart/reconciliation failure;
- analytics credential missing;
- insufficient analytics scope;
- wrong publication/account binding;
- provider API version incompatible;
- unsupported metric;
- duplicate ingestion;
- historical revision;
- collection restart;
- delayed metric;
- content removed/private;
- rate limiting;
- partially unavailable metrics;
- cross-workspace isolation;
- stale metric definition;
- experiment with insufficient sample;
- assignment imbalance;
- guardrail failure;
- observational result incorrectly requested as causal;
- changed experiment definition after exposure;
- LearningProposal attempting direct active-config mutation.

---

# Live smoke tests

Keep all external publishing and analytics checks opt-in.

Each provider must report independently:

`PASS`
`FAIL`
or
`NOT RUN: exact reason`

Do not convert missing credentials/scopes/API approval into PASS.

Never require live external accounts for normal CI.

---

# Documentation

Update:

- master README;
- architecture;
- publisher registry and adapter contracts;
- provider capability matrix;
- database/data lineage;
- analytics adapter contracts;
- metric semantics;
- attribution;
- cost attribution;
- experiment model;
- Growth Analyst;
- analytics memory;
- verification;
- limitations;
- Phase-11 learning handoff.

Clearly distinguish:

- deterministic fixture verified;
- live publishing verified;
- live analytics read verified;
- adapter implemented but not live tested;
- provider unavailable/not authorized.

---

# Definition of done

Phase 10 is complete when Salience can reliably execute:

`ReadyToPublishPackage`
`-> independently replaceable PublisherAdapter`
`-> canonical Publication`
`-> independently replaceable AnalyticsAdapter`
`-> immutable metric snapshots`
`-> full outcome attribution`
`-> governed experiment`
`-> deterministic experiment decision`
`-> immutable LearningProposal`

while preserving:

- replaceable publisher adapters;
- replaceable analytics adapters;
- no platform-specific canonical workflow branches;
- raw provider metric semantics;
- complete lineage;
- restart safety;
- idempotent publication and ingestion;
- account/workspace isolation;
- versioned capability and metric definitions;
- statistically honest experiment modes;
- no silent production self-modification.

Do not declare autonomous learning complete.

At completion report:

1. Phase-9 independent-review status;
2. production publisher enablement status by provider;
3. publisher adapter matrix;
4. analytics adapter matrix;
5. metric definitions/mappings;
6. migrations/entities;
7. attribution implementation;
8. experiment engine;
9. Growth Analyst behavior;
10. fixture E2E results;
11. live read/write smoke status separately;
12. PASS / FAIL / NOT RUN evidence;
13. exact reproduction commands;
14. known platform restrictions;
15. precise `LearningProposal` handoff for Phase 11.

The goal is not to produce one universal social metric or blindly optimize engagement.

The goal is to build a **provider-neutral, evidence-preserving performance layer** that can tell Salience exactly what happened, to which immutable content decisions it belongs, how confidently it can be interpreted, and what should be tested next.