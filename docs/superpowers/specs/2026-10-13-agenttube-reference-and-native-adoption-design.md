# AgentTube reference and native adoption design

**Status:** Bounded reference-only slice implemented; native adoption remains gated

**Date:** 2026-10-13

**Decision scope:** Preserve a pinned, reference-only upstream checkout and adopt selected product workflows through Salience's existing V4 contracts. User approval on 2026-10-13 released the first bounded submodule/evidence slice, now present in this checkout; it did not authorize implementation of native P2–P7 behavior or external effects.

## Purpose and success

Use the YouTube Automation Agent repository (also called AgentTube in the research reports) as a durable, inspectable product reference for onboarding, resumable content production, review, analytics, and experiments. Salience remains the runtime and system of record. Native adoption must fit the V4 governance, durable execution, recovery, publication, and evidence model rather than coupling Salience to the upstream application's runtime or database.

The reference-only slice is complete: the pinned checkout, durable source evidence, and repository-boundary checks are present. Native adoption succeeds only when each selected workflow is mapped to authoritative V4 requirements and qualified by its phase-specific evidence. Salience does not install, import, start, or credential the upstream project, and no adoption step changes today's effect, release, or merge gates. The submodule, upstream test results, and generic adapter interfaces do not establish native feature readiness.

## Evidence boundary

The reviewed upstream is `darkzOGx/youtube-automation-agent` at commit `e26792ae00dc1ec2434faf32632896609396dfad`, observed on `master`. The research describes a 69-file, single-channel YouTube application: Express, SQLite, local files, an in-process cron scheduler, sequential operator runs, and YouTube-specific publication and analytics. It offers useful product flows and durable stage checkpoints, but its checkpoints do not provide cross-process coordination or exactly-once provider effects. The audits also identify security and recovery limitations, including an all-interface bind with optional API authentication, plaintext local credentials, process-local concurrency guards, and outcome rows that can turn absent data into zero. Do not generalize its YouTube behavior into evidence of multi-platform support.

There are two distinct upstream verification records. The architecture and operations audits are source reviews and do not claim behavioral test execution. The later synthesis records a disposable checkout at the same pinned commit where `npm ci`, lint, and the 47-case system suite passed; no setup, OAuth, live provider, paid generation, or real publishing call ran. This establishes repeatable non-live behavior for that checkout only, not security approval, production readiness, or suitability as a Salience dependency.

The bounded reference-only slice is already implemented in this checkout: `.gitmodules` and the root gitlink point to the pinned commit, and the initialized reference checkout, tracked research, evidence manifest, verifier, and focused guard suite are present. The comparative audit records 13 boundary tests and the static verifier passing. These checks establish repository pinning and separation only. They do not mean Salience ran the AgentTube server or scheduler, and the recorded upstream package/lint/system results do not qualify a Salience runtime or production use.

The Salience gap audit inspected repository HEAD `e7a24a38ef920da3af071a51a288b859db9aaef1`. The V4 progress and blueprint bind the 116 focused and 614 full non-live results to application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4`; those results do not qualify the inspected HEAD. The remaining exact-head review/verification and open P1/RG0/RG1 work remain the immediate handoff. Current evidence supports local fixtures and design contracts, not production effects.

The binding current-state constraints are:

- `SALIENCE_EFFECTS_ENABLED=false`, production effects disabled, and publication budget zero.
- RG0 and RG1 remain HELD. P2–P7 remain gated; optional P6 remains disabled.
- `main` remains unmerged. No repository merge or production release is implied by this design.
- Completed P0 and local P1 increments are reused. Work resumes at the active P1 exact-head Task 10 review/verification and remaining acceptance obligations; it does not restart completed increments.

## Selected approach and alternatives

Keep the completed root-level, pinned reference submodule for source comparison, then implement selected product workflows natively against Salience's V4 contracts. This preserves upstream provenance without adding the upstream runtime to Salience's dependency or authority boundary. Adopt only the operator-facing behaviors that improve Salience; retain PostgreSQL, Temporal, outbox/inbox, scoped grants, typed recovery, rights, budgets, and release gates as the native foundation.

| Approach | Trade-off | Decision |
| --- | --- | --- |
| Import or package AgentTube as a runtime/service | Gives quick access to existing YouTube flows, but brings a single-process SQLite/cron design, broad local credential model, a YouTube-only contract, and independent security/recovery boundaries. | Rejected. It cannot own Salience workflows, data, credentials, or effects. |
| Fork or copy selected upstream code | Allows local changes, but creates source divergence, license/dependency obligations, and an ongoing merge burden while still requiring translation to Salience contracts. | Rejected for this adoption. Reconsider only through a separate reviewed proposal with a concrete code-level need. |
| Keep only written research, with no source reference | Avoids Git submodule mechanics, but loses an exact source snapshot and a simple reviewed-diff path for future comparisons. | Not selected. The pinned read-only reference is useful evidence when its boundary is enforced. |
| Pinned reference submodule; implement selected behavior natively | Adds a small source-navigation/update obligation, while preserving provenance and a clean runtime boundary. | Selected. |

## Submodule contract

The completed repository slice records `youtube-automation-agent/` at the Salience repository root with upstream URL `https://github.com/darkzOGx/youtube-automation-agent`, `branch = master` as an update hint only, and the superproject gitlink pinned at `e26792ae00dc1ec2434faf32632896609396dfad`. Builds and normal development never follow the branch head automatically. Future pin changes remain separate reviewed source changes.

The path is reference material only. Salience source, packaging, container images, and deployment must not import from it, copy it into a runtime artifact, install its dependencies, start its server or scheduler, or pass it credentials. Normal Salience CI checks out and tests Salience without initializing the submodule or installing its package graph. If a future comparative benchmark is useful, run it as a separately pinned, disposable, non-live, manually or schedule-triggered job with no Salience or provider credentials; it cannot qualify a V4 gate.

Do not adopt the upstream single-process runtime, SQLite or local-file persistence, cron or in-memory/process-local coordination, optional API authentication, credential model, database/schema, data, source code, or dependency graph. Native coordination and authority remain on the V4 PostgreSQL, Temporal, outbox/inbox, identity, and scoped-grant contracts. Any proposal to reuse upstream code or dependencies requires a separate concrete code-level proposal and license, dependency, security, and V4-boundary review before consideration; it does not inherit runtime or effect authority.

An upstream update is a deliberate source change, not a routine dependency refresh. It requires a reviewed commit diff from the old pin to the proposed pin, refreshed architecture and operations research, updated capability and gap findings, license and dependency/security review, and a new reviewed gitlink commit. Keep the branch hint separate from the pin. No upstream release, tag, or README claim changes Salience's eligibility or active routing.

## Native target architecture

The native product should expose a guided, durable workflow over Salience's existing canonical records and V4 phases. Each component has a narrow responsibility and persists versioned evidence through existing repository and workflow boundaries.

### Guided setup, capability manifests, and readiness

Provide an operator setup/readiness flow that discovers configured capability references and account connections, then writes a versioned, reviewable configuration proposal with an attached readiness report. It asks the operator to select the platform/account, provider or model, granted scopes, supported media, objective, region, and budget. It reports missing requirements and remediation; it never silently selects the first available provider or activates a route. Both proposal and report are advisory records, not authority: neither grants permission or a permit, activates a route, enables an effect, or changes a release gate.

Define a versioned capability-manifest/readiness contract for publisher, analytics, engagement, creative, and model adapters. A manifest identifies the adapter and contract version; supported operations and effect classes; platform, account and media constraints; authorization scopes; regional and quota limits; expected cost; version/fingerprint; and its idempotency, status, readback, cancellation, and reconciliation semantics. Secret values remain in the approved resolver; manifests and workflow history contain references only. Readiness reports bind their checks to a manifest/config revision, scope, time, expiry, result, cost, and remediation.

Readiness is versioned, expiring evidence bound to the proposal, manifest/config revision, scope, time, result, cost, and remediation; it is never authority. Default checks are local or read-only. Any private or billable probe requires explicit operator selection and a bounded cost notice. No probe grants a permit, enables production effects, clears RG0/RG1, or promotes a capability. Full candidate/evaluation/trial/active routing remains P5 work; the P2 contract supplies only the minimum manifest and readiness evidence needed to qualify concrete providers.

### Multi-platform adapters

Keep publisher, analytics, and engagement adapters distinct from creative/model providers. Each platform implementation owns its authentication scopes, quota, media format, visibility, localization, scheduling, upload/session, remote status, readback, cancellation, and correction behavior. A platform-neutral request or interface does not establish support by itself.

Keep the current rollout order: complete YouTube transfer/resume/readback/reconciliation, then qualify Instagram and LinkedIn as independent P2 adapters where official APIs and scoped access permit. TikTok remains a replaceable future candidate behind provider-neutral interfaces, not an initial rollout target or an implemented connector; any future qualification requires its own access, policy, and capability review. Keep unsupported operations visibly disabled. Record actual adapter/version, account, and capability used for every operation, including approved fallback as an explicit, policy-authorized deviation. No automatic provider substitution is permitted.

### Autonomous worker semantics

Use Temporal-backed cycle and child-workflow execution with PostgreSQL canonical state, transactional outbox/inbox, stable logical identities, and immutable run context. A parent cycle may create bounded, idempotent child tasks with explicit input scope, deadline, retry rules, concurrency lease, and resource ceiling. Independent research, analysis, and evaluation can fan out within configured bounds. Publishing, account mutation, paid generation, experiment activation, and release changes each require their own current authority and effect-specific gate.

Workers may propose plans, content, claims, provider choices, and learning hypotheses. Model or agent output is never a permit, approval, or release decision. Resume from the first valid incomplete stage using persisted artifacts and validation. An external operation retains its original operation identity across restart; an unknown result is reconciled against remote truth or held for review, never retried as a new operation. Worker termination, lease expiry, cancellation, and retry exhaustion produce durable typed state and preserve unresolved liabilities.

### Content and scene graph

Represent a production as a versioned graph of goal/cycle, evidence, brief, claims, scenes, generated or uploaded assets, transformations, platform-specific package variants, reviews, and publication operations. Every node has an immutable revision and content hash; every dependency edge pins the exact input revisions/hashes. Bind claims to source-evidence hashes and bind each scene and asset to its exact source/dependency hashes, verified bytes, provenance and transformation lineage, rights/consent/territory/expiry/revocation/disclosure decisions, model/provider identity and fingerprint, generation parameters, job identity, and actual cost where applicable.

Store media as immutable manifests over verified bytes. Scene repair or selective regeneration creates a new revision and invalidates only the transitive downstream outputs that depend on the changed hash; preserve and reuse unaffected verified work, the original version, and its review record. Each platform derivative is a separate immutable package bound to its exact source scene/asset/dependency hashes, platform contract and constraints, rights/provenance/cost/model lineage, and approvals. Factual review, quality review, and rights decisions remain distinct and bind to the content/package hash they approved. P2 design and acceptance must trace this graph and invalidation behavior to blueprint requirements R04, R20, R24, and R54, including their migration/read-compatibility and rollback constraints. Extend the blueprint through its requirement-change process only if a demonstrated gap remains; this umbrella spec does not create a parallel graph architecture or authority model.

### Analytics, learning, and experiments

Use a separate, read-only analytics subsystem and adapter per platform, isolated from publisher and engagement mutation paths. Normalize outcomes with metric definition, units, window, timezone, maturity, source coverage, pagination/cursor, and lineage. Preserve raw snapshots and corrections. Represent unavailable, unsupported, private, preliminary, simulated, missing, and measured zero as distinct states; an empty or absent response is not a zero. Calculate revenue or ROI only when the relevant revenue and cost evidence is present and comparable. Late data or corrections invalidate only dependent metrics, learning proposals, and evaluations through bounded, idempotent propagation.

Learning consumes eligible evidence and creates auditable, versioned proposals with supporting and contrary evidence, confidence, applicability, and source lineage. Review/approval only makes a proposal eligible for explicit downstream consideration; it cannot change cadence, spend, provider routing, content, an active strategy, or a release by itself. Corrections or revocations propagate to invalidate dependent proposals and evaluations; proposals, summaries, and evaluations cannot trigger their own cycle, experiment, refresh, or promotion. Experiments freeze their hypothesis, baseline/treatment bundle, eligibility, assignment unit, exposure method, sample/window/stopping rules, guardrails, and rollback target before activation. Persist assignment and actual exposure, deviations, control restoration, and result maturity. Inconclusive, invalid, or missing-exposure outcomes are explicit. Promotion is separate from evaluation and proposal approval, requires the existing G4 authorization and compare-and-swap release process, and retains a valid rollback or suspension path.

### Security, governance, and recovery

Reuse Salience's V4 identity, scoped grants, account bindings, policy versions, budget reservations/liabilities, outbox/inbox, immutable artifacts and receipts, typed cases, audit, and review contracts. Reauthorize at the named gate and immediately before dispatch against the exact workspace, account, connection, package hash, destination, rights, scopes, capability version, visibility, disclosure, budget, and current stop/revocation revision. Human approval is bound to the object and version reviewed; it does not replace per-asset rights or current dispatch checks.

Keep effect dispatch fail-closed. Persist the effect intent, stable ID, permit binding, budget liability, and outbox event atomically before delivery. Adapter retries follow declared idempotency and consistency horizons. Unknown outcomes, stale evidence, revoked rights, mismatched readback, and failed rollback open owned recovery cases. Stop controls prevent new permits; in-flight external effects are reconciled and their liabilities retained. Readiness, learning, experiments, and worker autonomy never bypass these controls.

## Staged roadmap mapped to V4

This adoption is additive to the current blueprint, not a replacement phase plan. The existing P1 queue and gate definitions remain authoritative.

The [program sequence](2026-10-13-v4-multiplatform-autonomy-program-sequence.md) is the current ordered navigation for this work; this adoption design does not override it.

This is an umbrella architecture decision, not one implementation unit. Do not create a single plan that spans P2–P7. The pinned reference submodule and its durable research/evidence record are already complete. Each future native vertical slice—readiness/manifests, scene graph and repair, each platform adapter, analytics/learning, experiments, and capability evolution—receives its own phase-owned design, acceptance tests, migration/rollback contract, and implementation plan when its existing V4 entry gate is reached.

| Order | Existing phase / gate | Native adoption work | Entry and exit boundary |
| --- | --- | --- | --- |
| 0 | Active P1 / RG1, plus RG0 | Resume exact-head Task 10 review and final local verification, then complete open P1 policy, production routing/revocation, schedule/fencing, recovery/alert, and external RG0/RG1 acceptance from the current matrix. | First work. Reuse completed P0/P1 increments and dated evidence; preserve zero effects and held release. No submodule or AgentTube workflow can close a P1 row. |
| 1 | P2 / RG2 | Add the minimum versioned capability/readiness proposals; deliver the native content/scene graph and selective repair mapped to R04/R20/R24/R54; complete the YouTube upload/readback/reconciliation path, then qualify each additional platform independently. | Starts only after applicable RG1 prerequisites. P2's existing creative, storage, and publication acceptance remains binding; unsupported adapters stay disabled. The blueprint remains the requirement authority; no parallel graph contract is created here. |
| 2 | P3 / RG3 | Add read-only analytics adapters, canonical metric/window and missingness semantics, outcome attribution, correction invalidation, and proposal-only learning. | Requires qualified publication evidence for live observation; fixture observations remain explicitly fixture-scoped. Learning does not alter production behavior. |
| 3 | P4 / RG4 | Add frozen strategy experiments with assignment/exposure, mature evaluation, guardrails, control restoration, separately authorized promotion, and rollback. | Requires P3 evidence and P1 context. No extra content cycles or self-promotion. |
| 4 | P5 / RG5 | Complete capability candidate/evaluation/trial/active/suspended/retired lifecycle, provider/protocol compatibility, explicit routing, and approved fallback/deviation records. | Each capability earns its own regression and release evidence. Configuration alone never activates a provider. |
| 5 | P6 / RG6, optional | No adoption work is enabled by this design. If separately chosen later, propose purpose-scoped data use, protected splits, isolation, cost limits, and candidate-only training outputs. | Disabled by default; normal operation must work with it disabled. Publication rights never imply training rights. |
| 6 | P7 / RG7 | Run constrained end-to-end rollout, restart/restore, load/soak, alerts, cost and rollback exercises for each enabled capability; start with one reviewed goal/account. | Requires RG0–RG5 and applicable earlier gates; RG6 applies only if training is explicitly enabled. Increase scope only with new approved evidence. |

Phase ownership, migrations, requirements, and verification remain in [the V4 implementation blueprint](../../v4/IMPLEMENTATION-BLUEPRINT.md) and [current progress ledger](../../v4/progress.md). This roadmap does not restart work already completed there or reinterpret a fixture pass as production qualification.

## Acceptance criteria

The design is ready to move into separate implementation planning only when each relevant criterion is represented by phase-owned evidence:

1. `.gitmodules` records the specified root path, upstream URL, and `master` update hint; the committed gitlink points exactly to `e26792ae00dc1ec2434faf32632896609396dfad`.
2. Normal Salience CI, packaging, and deployment do not initialize, install, import, copy, start, or credential the submodule. Any benchmark is isolated, disposable, non-live, and pinned.
3. An upstream pin change contains a reviewed old-to-new diff, refreshed architecture/operations/gap research, capability matrix, and license/dependency/security review.
4. The active P1 Task 10 and remaining acceptance sequence continues from the current worktree state; completed P0/P1 slices are not replayed. Current effect and gate constraints remain unchanged throughout adoption work.
5. Setup/readiness creates a reviewable versioned configuration/capability proposal and attached scoped readiness record; secret values never enter source control, workflow history, manifests, or readiness reports, and neither record carries authority.
6. Readiness results are revision- and scope-bound, expiring evidence. Running readiness cannot issue a publication permit, enable a capability, or change any release gate.
7. Every enabled adapter has a concrete capability manifest and independently tested operation, authorization, quota, cost, cancellation, remote-readback, and reconciliation behavior. Unsupported operations fail closed.
8. Planned versus actual provider/platform/version and every approved routing deviation are persisted; fallback is never silent.
9. Parent/child workers enforce durable identity, bounded fan-out, leases, retry/deadline/cost limits, lineage, and replay. An unknown external outcome reconciles or holds under the same operation identity.
10. Content, scene, claim, asset, rights, review, transformation, and platform-package lineage binds immutable node revisions and exact dependency hashes, including platform derivatives, provenance, rights, model/provider identity, and cost. A changed input invalidates only dependent outputs; acceptance maps to R04/R20/R24/R54 and preserves unaffected verified work.
11. Rights and factual review are separate, current, destination-aware, revocable, and bound to the approved content/package revision.
12. Separate read-only analytics preserve source lineage, windows, maturity, corrections, and the distinction between missing/unavailable and measured zero. Simulated or incomplete data cannot be presented as verified ROI.
13. Learning writes versioned proposals only; approval permits explicit consideration but not authority or self-triggering. Corrections invalidate dependent proposals/evaluations. Experiments freeze design and assignment before activation, measure actual exposure, restore control, and require separately authorized G4 promotion with rollback.
14. Stop/revocation, review, and recovery retain audit history and unresolved liabilities; rollback never silently retries or discards an external effect.
15. Migrations and rollback follow the existing additive V4 phase contracts, preserve historical identifiers and evidence, and never fabricate missing authority, provider outcomes, or analytics.
16. Every release claim cites exact source, configuration, dependency, account/scope, command, result, and artifact identity; fixture, upstream, private/sandbox, and production evidence remain visibly distinct.

## Migration and rollback

The completed reference submodule requires no Salience schema migration. A future pin update or removal changes only the superproject's `.gitmodules` and gitlink; reverting an update restores the prior reviewed pin. It does not change the application runtime or canonical records. Do not import AgentTube SQLite databases, credentials, media, or analytics as part of this adoption; any future data transfer requires a separate purpose, rights, schema mapping, and reconciliation design.

Native features use the existing P2–P7 migration boundaries. Add versioned sidecar records and readers, preserve old contracts, backfill only facts supported by source evidence, and mark unknown legacy provenance or authority explicitly. Use feature-level enablement only after the owning phase gate passes. For rollback, stop new work and dispatch, retain workflow histories, media manifests, review decisions, assignments, receipts, cases, and unsettled liabilities; keep compatible workers for already-started histories; rebuild projections from canonical records; and reconcile external operations before resuming. Do not use destructive schema downgrade or reset as routine rollback. Follow each phase's migration and rollback contract in the blueprint.

## Documentation and evidence

This design is informed by the tracked research reports reviewed against upstream `e26792ae00dc1ec2434faf32632896609396dfad`. Their paths and hashes are also bound by `docs/v4/agenttube-adoption-evidence.json`:

| Report | SHA-256 |
| --- | --- |
| `docs/research/youtube-agent/architecture-audit.md` | `23daa3962eda5f876be12f166966dc84fc06e9395d904992ff7672ed75564485` |
| `docs/research/youtube-agent/ops-quality-audit.md` | `6364f88a7acdb24e6e5f1449248a4b0c0e89c142441c8d460d255baa0eee28f8` |
| `docs/research/youtube-agent/salience-gap-audit.md` | `93c0822ca6ed4b965b699d8b28567ad51d537464783e3a482c9c621990fc331a` |
| `docs/research/youtube-agent/orchestrator-synthesis.md` | `8055a07720d022fdd469266c9e90ae69264b5b129f0f6c7cebfe7e359dea7a14` |

These tracked reports provide durable source provenance, while their hashes and the pinned gitlink remain reference evidence rather than release qualification. Before later native implementation, refresh the research only through the project's documentation/evidence process when the pin or evidence changes. Update this spec and the V4 blueprint together when a reviewed decision changes; keep historical progress and evidence immutable, and do not mark a phase complete from this design document. Upstream updates always require the reviewed diff and refreshed research described above.

## Exclusions

The reference-only submodule/evidence/guard slice is complete, but this design does not implement native P2–P7 behavior, change application code, install or adopt upstream dependencies, create or run an AgentTube service, access accounts or credentials, perform live or paid provider calls, enable publishing or autonomous effects, qualify an adapter, close RG0/RG1, open P2–P7, enable P6, merge to `main`, or import upstream data. Those actions require their own implementation work and existing phase-specific approvals/evidence.
