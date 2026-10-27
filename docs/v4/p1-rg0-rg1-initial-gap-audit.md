# Initial commit-bound P1/RG0/RG1 residual-gap audit

**Audit source:** `7c1ddcda280b510048ad61106c406171edd64f18` (`af010fba37debad65b9494abc3b70db0a6a2f915` tree) on `arch`, matching `origin/feat/archv4-p0-foundation` at audit start.

**Environment:** `p1-rg0-rg1-initial-7c1ddcd-ip-172-31-13-145`; manifest `18b15955b9a3be547bd1998ac2c1fce759c99d647fc3eeaa0ea21858e7a81436`. This is an effects-disabled development host with local PostgreSQL and Temporal fixtures. No production target, live account, production schedule inventory, external alert destination, or production credential set is configured.

**Disposition:** initial audit and bounded design evaluation are **COMPLETE_REVIEWED** for this frozen source/environment. All five increments are **REQUIRED** and all five designs are **COMPLETE_REVIEWED**; implementation and target qualification are **NOT RUN**. All 22 final-entry rows remain non-PASS; RG0 and full RG1 remain **HELD**; Slice 7 and P2 remain **BLOCKED**. This initial inventory cannot declare entry conditions passed. See the [portable review and orchestrator meta-review](p1-rg0-rg1-audit-and-spec-review.md).

**Whole-change documentation review:** **PASS_AFTER_FIX_ROUND_1** for reviewed candidate manifest `cafa0bd46ede2cc8aef51058f46541068a07cd031774c8b5501270bc2385ee9a`. The initial whole-change review found **0 Critical / 3 Important / 0 Minor**; bounded F1–F3 documentation/evidence fixes were applied; the scoped rereview passed with **0 new Critical / 0 new Important**. This documentation review is separate from the final exact-candidate/environment P1/RG0/RG1 audit, which remains **NOT RUN / BLOCKED** until required increments are implemented and current external acceptance evidence exists for one frozen final candidate/environment. Production remains disabled; RG0/RG1 remain HELD; P2/Slice 7 remain BLOCKED.

## Evidence boundaries

- Application qualification remains bound to `182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f`: 796 passed / 17 skipped / 3 warnings; local no-effects fixture scope only.
- That local application candidate has recorded image digest `c880a41e4a20f1322ad579b0b8d3f8b5ae14fcdf13635458d33cdc8c747e09a0` and wheel digest `425b7122f6431459a15bc4fe7bbdd169692b152356a0f36c6895e3fd452e3d04`. The audit source is separately frozen at `7c1ddcda280b510048ad61106c406171edd64f18`; no production candidate/artifact or final target environment was selected.
- Exact audit-head CI is current: [Actions run 40443346020](https://github.com/manojvermamv/Salience/actions/runs/40443346020) passed `verify` and `p0-regression` for `7c1ddcda280b510048ad61106c406171edd64f18`.
- CI does not replace formal approval or production acceptance. Formal PR approval is **NOT RUN**, `main` remains unmerged, production effects remain disabled, and fixture publication budget remains zero.
- Historical fixture evidence remains valid only in its recorded scope. Every row is stale or not run for final same-candidate/target-environment entry.

Each requirement in the JSON has structured `evidence_observations`. An observation records its qualified commit, artifact digest or explicit unavailability reason, environment reference and scope, repository-relative evidence path and SHA-256, source/verifier references, the separate observation made by this audit, and the precise reason it is stale for final entry. The nested current audit observation is bound to source commit `7c1ddcda280b510048ad61106c406171edd64f18` and environment manifest `18b15955b9a3be547bd1998ac2c1fce759c99d647fc3eeaa0ea21858e7a81436`; it explicitly records that no production artifact was selected.

## Row reconciliation

| Row | Gate | Audit status | Accountable owner role | Final-entry freshness | Required bounded work |
|---|---|---|---|---|---|
| R01 | RG0 | **NOT RUN** | platform/runtime owner (named owner missing) | STALE_OR_NOT_RUN_FOR_TARGET_ENVIRONMENT | production routing and worker fencing, external rg0 rg1 acceptance |
| R22 | RG0 | **NOT RUN** | security/identity owner (named owner missing) | STALE_OR_NOT_RUN_FOR_TARGET_ENVIRONMENT | external rg0 rg1 acceptance |
| R69 | RG0 | **NOT RUN** | data governance owner (named owner missing) | STALE_OR_NOT_RUN_FOR_TARGET_ENVIRONMENT | external rg0 rg1 acceptance |
| R16F | RG0 | **NOT RUN** | security/identity owner (named owner missing) | STALE_OR_NOT_RUN_FOR_TARGET_ENVIRONMENT | external rg0 rg1 acceptance |
| R23F | RG0 | **HELD** | policy/release owner (named owner missing) | STALE_OR_NOT_RUN_FOR_TARGET_ENVIRONMENT | production policy and cross service revocation, production routing and worker fencing, external rg0 rg1 acceptance |
| R02 | RG1 | **HELD** | workflow runtime owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production routing and worker fencing, external rg0 rg1 acceptance |
| R03 | RG1 | **HELD** | delivery runtime owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production routing and worker fencing, live temporal schedule fencing, external rg0 rg1 acceptance |
| R05 | RG1 | **HELD** | goal/baseline governance owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production policy and cross service revocation, external rg0 rg1 acceptance |
| R06 | RG1 | **HELD** | cadence identity owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production routing and worker fencing, live temporal schedule fencing, external rg0 rg1 acceptance |
| R07 | RG1 | **HELD** | admission owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production policy and cross service revocation, external rg0 rg1 acceptance |
| R08 | RG1 | **HELD** | schedule operations owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production routing and worker fencing, live temporal schedule fencing, external rg0 rg1 acceptance |
| R09 | RG1 | **HELD** | deferral/recovery owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production recovery remote read and alerts, external rg0 rg1 acceptance |
| R10 | RG1 | **HELD** | context owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production policy and cross service revocation, production routing and worker fencing, external rg0 rg1 acceptance |
| R13 | RG1 | **HELD** | authority/revocation owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production policy and cross service revocation, external rg0 rg1 acceptance |
| R15 | RG1 | **HELD** | policy owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production policy and cross service revocation, external rg0 rg1 acceptance |
| R17 | RG1 | **HELD** | effect dispatch owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production policy and cross service revocation, production routing and worker fencing, external rg0 rg1 acceptance |
| R18 | RG1 | **HELD** | safety/reconciliation owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production policy and cross service revocation, live temporal schedule fencing, production recovery remote read and alerts, external rg0 rg1 acceptance |
| R32 | RG1 | **HELD** | recovery operations owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production recovery remote read and alerts, external rg0 rg1 acceptance |
| R33 | RG1 | **HELD** | recovery command owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production recovery remote read and alerts, external rg0 rg1 acceptance |
| R34 | RG1 | **HELD** | alert/on-call owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production recovery remote read and alerts, external rg0 rg1 acceptance |
| R35 | RG1 | **HELD** | records/retention owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production recovery remote read and alerts, external rg0 rg1 acceptance |
| R53 | RG1 | **HELD** | data contract owner (named owner missing) | STALE_OR_DIFFERENT_CANDIDATE_ENVIRONMENT | production routing and worker fencing, production recovery remote read and alerts, external rg0 rg1 acceptance |

Every row retains its detailed requirement, historical evidence scope, remaining dependency, and increment mapping in [`p1-rg0-rg1-initial-gap-audit.json`](p1-rg0-rg1-initial-gap-audit.json). A role is assigned for planning, but no named production owner is recorded; that absence is a blocker, not an inferred assignment.

## Mandatory RG0 external checks

The five external checks remain independent gate inputs. The source status is the status recorded by `p0-release.json` at the frozen audit commit. Historical local or stale results are scoped evidence only; `current status` is this initial audit's disposition for the final candidate and target environment.

| External check | Source / historical status | Current status and freshness | Accountable role; missing human | Exact RG0 row mapping | Remaining dependency; increment |
|---|---|---|---|---|---|
| Independent safety review | Source **NOT RUN**; engineering reviews cover local fixture changes only | **NOT RUN**, no final target evidence | Independent production safety/deployment reviewer; human missing | R01; review controls must cover every row | Named independent reviewer, exact candidate/environment review, findings/disposition and signed evidence; `external_rg0_rg1_acceptance` |
| Production identity and ingress | Source **NOT RUN**; local identity/fixture checks do not transfer | **NOT RUN**, no final target evidence | Security/identity owner; human missing | R16F, R22 | Approved issuer/audience, TLS ingress, scoped database role, approved identity/account, rotation owner and negative-scope results; `external_rg0_rg1_acceptance` |
| Production storage and restore | Source **NOT RUN**; disposable real-S3 qualification is local only | **NOT RUN**, no production restore | Storage/backup and data-governance owner; human missing | R69; R01 is supporting deployment coverage | Managed encryption/configuration, backup identity, retention/jurisdiction result, restore and rollback readback; `external_rg0_rg1_acceptance` |
| Production collector and alert delivery | Source **NOT RUN**; local SDK export/correlation only | **NOT RUN**, no external destination or acknowledgement | Observability and on-call owner; human missing | R01; related P1 behavior is R18, R32 and R34 | Approved destination, correlated no-secret event, delivery receipt, named acknowledgement and escalation; `external_rg0_rg1_acceptance` |
| Remote repository gate enforcement | Source snapshot says **PASS** only for the older published base; the recorded readback is stale and narrow | **NOT RUN** for current target protection/formal approval; audit-head CI is CI-only | Repository administrator/release owner; human missing | R01 | Current strict protection/required-check readback on the target and exact candidate, plus separate formal PR approval; `external_rg0_rg1_acceptance` |

The remote-enforcement PASS is preserved only as historical evidence for published base `20be7815d5e5c95fea2161c908ce8ac5cae88ce6`; the retained readback record is `docs/v4/repository-enforcement-evidence.json` for commit `4bbffb61338d27a9da59ec07154e67794007e8cb`. It does not transfer to this audit candidate. `p0-release.json` keeps the current external check at **NOT RUN**.

## Current entry, preservation, and deferred work

All 22 rows now separate `current_entry_residual`, `preservation_obligations`, and `deferred_phase_owned_work`. The current-entry field contains only the evidence and controls needed to close that row's present P1/RG0/RG1 contract. Preservation fields keep the proven fixture behavior and safety boundaries intact while increments are built.

| Row | Current entry residual | Explicitly deferred phase-owned work |
|---|---|---|
| R03 | Production route/worker ownership, live schedule inventory/fencing, scoped remote reconciliation and same-target RG1 acceptance remain unqualified. | P2 owns provider charge/effect integration and enabled-provider qualification; it is not required to enter P2. |
| R17 | Same-candidate no-effects proof is still needed for one bounded permit claim, stop/revocation serialization and fail-closed behavior, with a named owner and target. | P2 owns provider charge/effect, ready-package/outbox integration and provider qualification; effects stay disabled until P2's own acceptance. |
| R35 | Current P1 terminal archive reason/evidence/retention/idempotency/no-reuse controls have fixture evidence only; final candidate/environment evidence is absent. | Rights-filtered discovery and later record families have no exact phase assigned in the source. Name their phase owner and row mapping before shipping those extensions; they do not gate P2 entry. |
| R53 | Current P1 envelope uniqueness, corrections, immutable receipts and audited pointers lack refreshed same-candidate/environment evidence. | Later record families need an assigned phase and envelope/migration tests before implementation; they do not gate P2 entry. |

The same three fields are present for every other row; any later trial behavior is conditional on trial enablement, and later capability families retain their own gates. P2-owned implementation cannot be a prerequisite to entering P2. Entry remains blocked by each current non-PASS, stale, missing-owner, or environment-mismatched RG0/RG1 row and the required P1 increments; provider effects remain disabled until P2-specific exact-candidate/environment acceptance passes.

## Required bounded increments

### 1. Production Policy And Cross Service Revocation

**Decision:** REQUIRED

Local current-authority, stop and fixture-policy checks exist, but enabled production policy, cross-service revocation propagation, pre-dispatch reauthorization and in-flight liability reconciliation remain unqualified.

**Primary rows:** R13, R15, R17, R18.
**Supporting rows:** R05, R07, R10, R23F.
**Specification:** [`2026-10-13-p1-production-policy-and-revocation-design.md`](../superpowers/specs/2026-10-13-p1-production-policy-and-revocation-design.md)

**Reuse instead of rebuilding:** `src/salience/governance/policy.py`, `src/salience/cycles/authority.py`, `src/salience/cycles/governance.py`, `src/salience/publication/governance.py`.

### 2. Production Routing And Worker Fencing

**Decision:** REQUIRED

Exact local artifact and pre-registration fences are qualified, while named production routes, worker ownership/registration, drain, compatible floor readback and rollback are not.

**Primary rows:** R02, R03, R06, R08.
**Supporting rows:** R01, R10, R17, R23F, R53.
**Specification:** [`2026-10-13-p1-production-routing-and-worker-fencing-design.md`](../superpowers/specs/2026-10-13-p1-production-routing-and-worker-fencing-design.md)

**Reuse instead of rebuilding:** `src/salience/cycles/publication_recovery_compatibility.py`, `scripts/launch-v4-publication-worker.py`, `deployment/v4/publication-worker-launch-lock.json`, `src/salience/workflows/worker.py`.

### 3. Live Temporal Schedule Fencing

**Decision:** REQUIRED

One-goal fixture and pinned no-effects Temporal cutovers are tested, but authorized target inventory, production pause/readback, stop, cutover, monitoring and rollback fencing are absent.

**Primary rows:** R03, R06, R08, R18.
**Specification:** [`2026-10-13-p1-live-schedule-fencing-design.md`](../superpowers/specs/2026-10-13-p1-live-schedule-fencing-design.md)

**Reuse instead of rebuilding:** `src/salience/cycles/schedule_cutover.py`, `src/salience/cycles/temporal_schedule_control.py`.

### 4. Production Recovery Remote Read And Alerts

**Decision:** REQUIRED

Immutable local holds, exact replay and database notification acceptance exist, but production recovery routing, least-scope remote reads, external delivery/acknowledgment and escalation are absent.

**Primary rows:** R18, R32, R33, R34.
**Supporting rows:** R09, R35, R53.
**Specification:** [`2026-10-13-p1-production-recovery-and-alerts-design.md`](../superpowers/specs/2026-10-13-p1-production-recovery-and-alerts-design.md)

**Reuse instead of rebuilding:** `src/salience/cycles/publication_recovery.py`, `src/salience/cycles/legacy_publication_runtime.py`, `src/salience/cycles/runtime_waits.py`.

### 5. External Rg0 Rg1 Acceptance

**Decision:** REQUIRED

The current ledgers list held/not-run checks but lack one executable, owner-bound, exact-candidate/deployment acceptance contract and matching production evidence.

**Primary rows:** R01, R22, R69, R16F, R23F, R02, R03, R05, R06, R07, R08, R09, R10, R13, R15, R17, R18, R32, R33, R34, R35, R53.
**Specification:** [`2026-10-13-rg0-rg1-external-acceptance-design.md`](../superpowers/specs/2026-10-13-rg0-rg1-external-acceptance-design.md)

**Reuse instead of rebuilding:** `docs/v4/p0-release.json`, `docs/v4/p1-qualification-matrix.json`, `docs/v4/repository-enforcement-evidence.json`.

## Final-audit rule

After all required increments and external acceptance are implemented and evidenced, freeze one final candidate and one target environment and repeat every row. Any `FAIL`, `NOT RUN`, `HELD`, stale result, environment mismatch, missing named owner, missing rollback proof, or evidence from another candidate keeps P2 blocked. Candidate or environment changes invalidate affected evidence and require rerun.

## Audit method

- Read the current blueprint, progress, release ledgers, qualification matrix, source locks, evidence artifacts, current source/tests/migrations, and exact-head CI.
- Ran three independent read-only domain audits: policy/routing; Temporal/recovery; and external acceptance. The exact policy/routing report bytes are retained in [`salience-p1-policy-routing-audit.md.txt`](audits/salience-p1-policy-routing-audit.md.txt) (SHA-256 `af34376c35348d102576dcccbc90165396e9611cd3e5cf0e4fb5be7d02cd0572`) and indexed by its [portable Markdown wrapper](audits/salience-p1-policy-routing-audit.md) (wrapper SHA-256 `0a538f5cfac2c8bbd59556fbb227258157b9c4abdaea002711bad71682b1a460`). The Temporal/recovery and external-acceptance report bytes are retained at [`docs/v4/audits/salience-p1-temporal-recovery-audit.md`](audits/salience-p1-temporal-recovery-audit.md) (`0370638903bff6006ff9fe29dec3d91d86956fd8618b8ef41694fbab1181133c`) and [`docs/v4/audits/salience-p1-external-evidence-audit.md`](audits/salience-p1-external-evidence-audit.md) (`b29b8135af40e8f95f5e9b903ea70d8840ec95c3b01eaf684004986c9e75997b`). The `/tmp/salience-p1-*-audit.md` files are scratch references only; durable repository paths and hashes are recorded in the audit manifests.
- Built a curated host-system knowledge graph from the exact commit, excluding `.agents`, the immutable AgentTube reference submodule, historical archives, and generated visual artifacts.
- No production endpoint, live schedule, production database, provider, account, alert channel, or credential was accessed.
- No historical evidence was rewritten, and no local fixture result was promoted to production PASS.

## Review and next gate

Independent row-by-row review and cross-spec review passed; the orchestrator meta-review accepts the completed initial audit/spec design evaluation only. Documentation whole-change review is PASS_AFTER_FIX_ROUND_1 for reviewer-reported manifest `cafa0bd46ede2cc8aef51058f46541068a07cd031774c8b5501270bc2385ee9a`; that review does not transfer to later content or exact-candidate CI. The final exact-candidate/environment P1/RG0/RG1 audit remains NOT RUN / BLOCKED. Implementation and target qualification remain NOT RUN. The audit and five designs are COMPLETE_REVIEWED, but implementation and target qualification are NOT RUN. All 22 final-entry rows remain non-PASS, so RG0/RG1 remain HELD and Slice 7/P2 remain BLOCKED. The designs do not authorize production effects, live qualification, release, implementation, or a `main` merge.
