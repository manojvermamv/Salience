# Task 10 Brief: Local RG0/RG1 External Acceptance Package Checker

## Intent and boundary

Build a local, deterministic validator for one immutable external acceptance package covering the five RG0 rows and 17 P1/RG1 rows below. It validates package structure, exact identity bindings, evidence references, scope, statuses, dependencies, freshness, invalidation, transitions, and a separate final audit record. It has no external client and never writes the release or qualification ledgers, application state, or a release gate. A structurally valid synthetic package proves only local checker behavior. The current ledgers remain held, so external disposition is NO_GO.

## Exact row contract

The package contains each ID once, with the specified gate:

- RG0: R01, R22, R69, R16F, R23F.
- P1/RG1: R02, R03, R05, R06, R07, R08, R09, R10, R13, R15, R17, R18, R32, R33, R34, R35, R53.

The RG1 dependency sets are reconciled exactly from docs/v4/p1-qualification-matrix.json:

| Row | Dependencies |
| --- | --- |
| R02 | P0, R01 |
| R03 | P0, R01 |
| R05 | P0, R01 |
| R06 | R03, R05 |
| R07 | R05, R06 |
| R08 | R07 |
| R09 | R07, R08 |
| R10 | R07 |
| R13 | R10 |
| R15 | P0, R05 |
| R17 | R03, R15 |
| R18 | R17 |
| R32 | R03, R15 |
| R33 | R17, R32 |
| R34 | R32, R33 |
| R35 | R32 |
| R53 | P0, R03 |

A row may be PASS only when every declared prerequisite is exact-scope and PASS. A prerequisite absent, held, not run, failed, stale, or mismatched blocks the row. The checker reads current P0/RG0 state from p0-release.json and P1/RG1 state and dependencies from the qualification matrix; it cannot promote either ledger.

## Immutable package, schema, and CLI contract

The top-level JSON package is closed-schema and contains:

1. schema_version, run_id, a content-addressed manifest plus its SHA-256, an artifact digest index, exactly 22 row records, append-only invalidation lineage, and an optional separate final-audit record.
2. The manifest binds full 40-hex source commit and tree IDs; signed build/provenance references; every deployed image, wheel, launcher, configuration bundle, source/artifact lock and SHA-256 with candidate-to-artifact mapping; exact migration head; deployment/release ID and deployed services/workers; database/schema and Temporal target/namespace; stable environment ID, stage, region, tenant/workspace and data/jurisdiction scope; effect state fixed to production_effects_enabled=false and publication_budget=0; and immutable environment/deployment readbacks. Artifact/evidence references carry an absolute URI, SHA-256, and immutable-retention/content-addressed proof.
3. Each row binds its gate, exact manifest identity and resource/account scope; exact dependencies and prerequisite evidence IDs; named human owner, authorized human operator, and independent human reviewer with verified identity/authorization and UTC scope; pinned runbook URI/SHA, exact executable/version/argv or API method/request digest, expected predicate, principal, time window and command/request/trace IDs; redacted raw result and independent readback references with SHA-256; status, scope match and freshness; transition/rollback evidence; observability references; and row-specific reason. route_id is a UUID; route evidence also binds route_tuple_sha256 and the authenticated route-manifest digest.
4. Each evidence reference has a URI and SHA-256. Raw outputs are redacted before hashing; credential values, tokens and private keys are forbidden. No evidence may be inherited from another run or scope. Fixture, CI, formal approval, historical-candidate and external-production evidence remain distinct classes and cannot transfer across candidate, deployment, environment, account, tenant, namespace, provider, repository, or resource.
5. Exactly one status is used per row/check: PASS, FAIL, NOT RUN, or HELD; freshness is separately CURRENT or STALE. Mutable observations require a positive numeric age limit and an unexpired, approved, versioned owner policy. Stale evidence cannot pass. Missing prerequisites, scope, owners, credentials/identity, procedure, digest, result, readback, rollback, freshness approval, or telemetry yield HELD/NOT RUN and aggregate NO_GO.
6. Procedures declare exactly NO_MUTATION or a preauthorized planned transition. NO_MUTATION requires immutable beginning/end readbacks with equal state SHA-256, no mutation IDs, owner/reviewer acknowledgement, and no unexplained effect. A planned transition binds the approved ticket, exact target/scope, expected pre/post versions, actor/command receipt, independent final readback, and rollback when restoration is required. An unexpected or subsequent change invalidates evidence. Revoked/rotated credentials or policy versions stay revoked; an old credential is never restored.
7. Invalidation lineage is append-only and digest-bound. A candidate, deployment, environment, common manifest, or shared procedure/checker change invalidates the entire run. A row-specific change invalidates that row and the transitive dependency closure. Reruns have new run IDs and preserve invalidated run IDs, digests, and reasons; no prior PASS is copied forward.
8. Negative controls require a raw proven denial plus an independent zero-call/zero-effect readback. Observability references correlate UTC time, actor, run, candidate, environment/account alias, command/request, trace, route/schedule/operation and external receipt IDs without secrets. Alert delivery, human acknowledgement, and gate approval are separate evidence.
9. Final audit is a distinct record bound to the immutable package digest. It independently lists and rechecks all 22 rows, scope, evidence hashes, freshness, required increment evidence, readbacks and the two release ledgers. It cannot change gates or substitute for the later exact-candidate/environment audit.
10. Local reconciliation is read-only. Current p0-release.json has production_effects_enabled=false, RG0/RG1 HELD, and five external checks NOT RUN; current p1-qualification-matrix.json has RG1 HELD and every one of the 17 in-scope RG1 rows HELD. Any ledger hold/mismatch keeps aggregate disposition NO_GO. Deterministic CLI:
    python scripts/check-p1-external-acceptance.py --package <path> --schema <path>.
    It prints sorted machine-readable JSON, makes no network calls or file mutations, and exits nonzero for NO_GO or invalid input. It reports local structural validity separately from external disposition and never emits a gate GO.

## Status, invalidation, and no-mutation rules

FAIL preserves an executed failed assertion; NOT RUN means no execution/raw result; HELD means missing, unsafe, ambiguous, or uninterpretable prerequisites; STALE is a separate freshness value and makes a row non-pass. Scope match is exact and separate from status. Missing or unapproved max age, age expiry, unplanned drift, common identity change, or evidence captured for a different candidate/deployment/environment makes evidence stale and blocks the affected dependency closure. An exact predeclared and verified transition is the sole transition exception. An expected denial passes a negative control only with raw denial and independently proven zero effect. The checker validates records and hashes without authenticating remote systems or inferring a row PASS from child evidence.

## Migration and rollback

None. The checker adds no application migration, table, fixture-state change, external procedure, or application-state mutation. Package/evidence rollback is not destructive: historical packages and hashes are append-only, invalidations are appended with a reason, and a rerun uses a new run ID. Any test credential revocation remains in force; no old credential or policy revision is restored. The checker never mutates p0-release.json, p1-qualification-matrix.json, production state, or a release gate.

## Required acceptance tests (all 20)

1. test_package_requires_exact_candidate_tree_artifacts_deployment_and_environment
2. test_package_requires_exact_row_set_once
3. test_each_row_requires_named_authorized_owner_operator_and_independent_reviewer
4. test_procedure_is_pinned_and_executable
5. test_raw_results_and_rollback_are_hashed_and_referenced
6. test_scope_transfer_across_fixture_ci_commit_environment_account_is_rejected
7. test_ci_formal_approval_and_external_acceptance_remain_distinct
8. test_fail_not_run_held_stale_or_scope_mismatch_blocks_package
9. test_missing_or_unapproved_freshness_limit_holds_mutable_evidence
10. test_planned_revocation_with_verified_final_denial_passes
11. test_unplanned_or_subsequent_revision_change_fails
12. test_no_mutation_requires_equal_start_and_end_state
13. test_revoked_credentials_are_never_restored
14. test_run_invalidation_is_append_only_and_transitive
15. test_negative_controls_pass_only_on_proven_denial_and_zero_effect
16. test_package_cannot_enable_production_or_release_a_gate
17. test_final_audit_is_separate_and_rechecks_every_row
18. test_rollback_requires_pre_state_action_and_exact_post_state_readback
19. test_observability_artifacts_are_correlated_and_secret_free
20. test_default_package_adds_no_application_migration_and_excludes_later_phases

