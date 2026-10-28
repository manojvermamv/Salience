# Task 10 Report: P1 External Acceptance Package Checker

## Result

Implemented the local RG0/RG1 acceptance-package checker from base `7df79f71cb2b0473566f705036ab64e66372ff01`. The public validator is a 325-line facade over private contract, canonical-reference, reconciliation, row-validation, manifest, and audit modules. The closed schema requires the exact 22 row IDs; the CLI reads local files only and never changes application state, production state, `p0-release.json`, `p1-qualification-matrix.json`, or a gate.

The checker keeps local structural validity separate from the current external disposition. A valid synthetic package remains `NO_GO`; current P0 external checks are all `NOT RUN`, and the P0/RG0 and P1/RG1 ledgers remain `HELD`. The checker requires denial plus independent zero-effect evidence for the R22, R16F, and R23F negative controls, blocks dependent rows when a prerequisite is not current and passing, and requires the separate final audit to authorize an independent auditor and re-read every current passing row.

## RED/GREEN evidence

The original 20 acceptance tests were run serially before implementation:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short
```

Result: **20 failed**, because `salience.acceptance` did not exist yet.

The ten accepted review findings were covered by focused regressions and exercised RED before their fixes, then GREEN. Final focused run, including those regressions and the NOT RUN/HELD representation case:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short
```

Result: **31 passed in 66.91s**. The environment variables prevent the root pytest hook from provisioning host services; tests ran serially.

Two final review regressions captured the remaining failures before correction. The NOT RUN/HELD test initially reported `1 failed` because omitted execution observations were treated as evidence-binding errors. The secret-scan CLI regression initially reported `1 failed` because caller-schema diagnostics echoed a Bearer-shaped row status before the package secret scan. Both focused tests passed after correction. The CLI now scans the complete parsed package before supplied-schema validation or package-derived output.

The focused review regressions are:

```text
test_reconciliation_rejects_duplicate_matrix_ids_and_wrong_p0_contract
test_deployment_artifacts_and_row_routes_resolve_to_authenticated_inventory
test_authority_scope_and_execution_principals_bind_exactly_or_use_delegation
test_planned_transition_final_state_and_version_match_freshness_readback
test_no_mutation_requires_empty_resolved_liabilities
test_lineage_requires_explicit_root_or_predecessor_invalidation_history
test_all_evidence_references_bind_to_run_and_exact_candidate_scope
test_primary_and_independent_readback_evidence_are_distinct
test_secret_scan_covers_normalized_package_and_uri_components_without_echo
test_final_auditor_differs_from_every_row_authority
test_not_run_and_held_rows_need_no_dummy_execution_evidence_but_pass_fail_stay_strict
```

Each was run with the host-safe pytest command above and `-k <test_name>` while its invariant was introduced.

Schema validation used `Draft202012Validator.check_schema` on `docs/v4/p1-rg0-rg1-external-acceptance.schema.json` and passed. The explicit CLI controls generated sealed packages in a temporary directory and ran the checker with the supplied schema. Results:

```text
/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package <temporary-package.json> --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
```

The synthetic NOT RUN package emitted one-line JSON with `structural_status=VALID`, `disposition=NO_GO`, and `gate_state=UNCHANGED`; exit **1**. A malformed PASS package emitted `structural_status=INVALID`; exit **2**. A Bearer-shaped secret placed in a schema-invalid status emitted `SECRET_FOUND_IN_EVIDENCE`, did not echo the marker, and exited **2**.

The invalid-argument control ran `/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py` without either required option; it emitted JSON with `disposition=NO_GO` and exited **2**.

`py_compile` passed for the acceptance package and CLI. `git diff --check` passed. Generated `__pycache__` directories were removed. Ruff was unavailable in the project virtualenv (`No module named ruff`).

## Cleanup and concerns

Temporary CLI packages were created in `TemporaryDirectory` or pytest `tmp_path` scopes and removed automatically. Generated `__pycache__` and pytest cache directories were removed before commit. No application migration or application-state change was added; no external client was used; ledger bytes were checked by tests and remain unchanged. The branch was not pushed.

An initial unguarded pytest attempt invoked the repository session hook, which reported Docker services healthy and attempted an Alembic setup; that setup failed with `ModuleNotFoundError: salience`. Later runs used the host-safe guard above. I left any containers alone because they may have pre-existed the attempt.

The checker verifies signature and external-evidence references structurally, but it cannot authenticate a remote signature or independently inspect external object contents without the trust keys and target-specific evidence. It therefore treats synthetic data as local structure only and preserves the current `NO_GO`/ledger holds.

## Review fix round 2

The four review regressions were each run before their corresponding fixes with the host-safe command form below and a single `-k <test_name>` selector. Each RED run reported **1 failed, 34 deselected**:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k test_scope_bindings_cover_manifest_axes_and_route_applicability
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k test_planned_transition_and_rollback_bind_exact_row_scope
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k test_final_audit_rechecks_use_new_independent_evidence
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k test_fail_status_requires_an_executed_failed_normalized_assertion
```

These cases cover full candidate/deployment/environment/account/resource/migration scope axes and closed route applicability; structured transition target and rollback scope; new, independently sourced, route-bound final-audit rechecks; and FAIL rows with at least one executed failed normalized assertion. `NOT RUN`/`HELD` rows remain structurally representable without execution-only evidence and still produce `NO_GO`.

Focused green runs:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k 'scope_bindings_cover_manifest_axes_and_route_applicability or planned_transition_and_rollback_bind_exact_row_scope or final_audit_rechecks_use_new_independent_evidence or fail_status_requires_an_executed_failed_normalized_assertion'
```

Result: **4 passed, 31 deselected in 15.30s** on the final implementation. A related transition/rollback batch reported **10 passed, 25 deselected in 32.29s**. After all schema and fixture alignment, the final serial Task 10 run was:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short
```

Result: **35 passed in 135.69s**.

Final schema and CLI controls passed after the full-scope rollback schema change. `Draft202012Validator.check_schema` passed. With temporary sealed packages and the supplied schema, the CLI returned: synthetic structurally valid package **exit 1 / VALID / NO_GO**; forbidden application-migration package **exit 2 / INVALID**; Bearer-shaped secret **exit 2 / SECRET_FOUND_IN_EVIDENCE / marker not echoed**; missing required arguments **exit 2 / NO_GO**. The CLI remains local-only and does not mutate either ledger or any gate.

Final `compileall` for `src/salience/acceptance` and `scripts/check-p1-external-acceptance.py` passed, as did `git diff --check`. No app migration, external client, production mutation, or gate mutation was added. Temporary CLI files were removed by temporary-directory cleanup. Generated source/test `__pycache__` directories were removed before commit. This round has not been pushed.


## Review fix round 3: final-audit evidence independence

The final-audit recheck invariant now enumerates every URI evidence reference recursively from the matched row object and applies the existing `_independent` check to each reference. The audit recheck itself remains outside the enumerated object. No schema change was needed because the closed schema already represents every covered reference. The test fixture binder now preserves explicitly spoofed evidence IDs so the regression proves same-content reuse is rejected even when superficial evidence/source identifiers are changed.

The expanded regression first failed against the prior checker as intended. It exercised every URI reference in a fully populated R08 planned-transition row and R23F negative-control row, including dependency, authorization, runbook, normalized-check, freshness, planned transition, rollback, observability, and negative-control evidence. The RED result listed **39 accepted row-evidence reuses**.

RED command and result:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k test_final_audit_rechecks_use_new_independent_evidence
1 failed, 34 deselected in 51.68s
```

After the recursive row-reference check, the same focused command passed: **1 passed, 34 deselected in 52.80s**. The complete serial Task 10 suite then passed:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short
35 passed in 184.24s (0:03:04)
```

Schema and CLI controls passed. `Draft202012Validator.check_schema(json.loads(Path("docs/v4/p1-rg0-rg1-external-acceptance.schema.json").read_text(encoding="utf-8")))` exited 0. CLI invocations used the supplied schema:

```text
/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-p1-task10-round3-cli-controls/synthetic-valid.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 1; structural_status=VALID; disposition=NO_GO; gate_state=UNCHANGED

/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-p1-task10-round3-cli-controls/malformed.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 2; SCHEMA_VALIDATION_FAILED; structural_status=INVALID; disposition=NO_GO

/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-p1-task10-round3-cli-controls/secret.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 2; SECRET_FOUND_IN_EVIDENCE; marker not echoed

/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py
exit 2; ARGUMENTS_MISSING; disposition=NO_GO
```

`compileall` for `src/salience/acceptance` and the checker CLI passed, as did `git diff --check`. Generated `__pycache__` directories under `src`, `scripts`, and `tests` were removed. No application migration, external client, production/gate mutation, or ledger modification was added.


## Post-completion route-inventory cardinality correction

The external contract fixes the acceptance row set at 22; it does not require 22 deployed routes. The schema had incorrectly tied both `manifest.route_manifest.inventory` and the `ROUTE_SET` scope binding to exactly 22 entries. Both arrays now require at least one entry and have no route-count maximum. Existing runtime checks in `_manifest_validation.py` still enforce unique route IDs, exact worker-to-inventory assignment, and exact row route UUID/tuple digest/manifest membership. The unit fixture now uses one authenticated route for all 22 rows by default; tests that need cross-route evidence opt into an explicit distributed inventory.

The acceptance regression was RED against the old schema for both supported layouts:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k test_route_inventory_cardinality_is_independent_of_row_count
2 failed, 36 deselected in 1.64s
```

After the schema correction, the focused route inventory checks passed, covering one route shared by all rows, a unique two-route inventory with row reuse, and rejection of empty inventory, duplicate route IDs, worker/inventory mismatch, and an out-of-inventory row route:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k route_inventory
3 passed, 35 deselected in 8.80s
```

The complete serial Task 10 suite passed:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short
38 passed in 175.68s (0:02:55)
```

`Draft202012Validator.check_schema` passed, and `validate_payload_against_schema` returned no errors for the one-route and two-route fixtures. Direct CLI controls with the supplied schema returned:

```text
/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-route-inventory-cli-controls/valid-1-routes.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 1; structural_status=VALID; disposition=NO_GO

/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-route-inventory-cli-controls/valid-2-routes.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 1; structural_status=VALID; disposition=NO_GO

/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-route-inventory-cli-controls/empty-inventory.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 2; structural_status=INVALID; disposition=NO_GO; SCHEMA_VALIDATION_FAILED
```

P0 release and P1 qualification ledger bytes were unchanged by CLI checks. `compileall` and `git diff --check` passed. Temporary CLI fixtures and generated `__pycache__` directories were removed. No ledger, production, or gate mutation was made.

## Route UUID canonical-spelling review fix

Authenticated route IDs must now use canonical lowercase hyphenated UUID text (`str(UUID(value)) == value`). The runtime applies this rule to route inventory entries, worker route assignments, row route references, and the other route-ID references covered by the closed schema. The schema adds the corresponding lowercase UUID pattern alongside its UUID format checks. Route cardinality remains independent of the fixed 22-row count, and row reuse plus exact worker/inventory matching are unchanged.

The semantic-duplicate regression was RED before the runtime fix: an inventory and worker assignment containing lowercase and uppercase spellings of the same UUID were accepted as structurally `VALID`.

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k test_route_inventory_rejects_semantic_uuid_duplicates
1 failed, 38 deselected in 1.23s
```

Before implementation, the expanded canonical-reference regressions also failed for semantic duplicate inventory IDs and mixed-case worker, row, correlation, and authorization route references:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k 'semantic_uuid_duplicates or route_uuid_references_require_canonical_text'
5 failed, 38 deselected in 8.03s
```

After implementation, the expanded regression covers inventory, worker, row, observability correlation, authorization route binding, and final-audit route-set references, and passes:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short -k 'semantic_uuid_duplicates or route_uuid_references_require_canonical_text'
7 passed, 38 deselected in 11.93s
```

The full serial Task 10 suite passed:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 /home/admin/Salience/.venv/bin/python -m pytest tests/unit/test_p1_external_acceptance.py -q --tb=short
45 passed in 190.46s (0:03:10)
```

Schema and direct CLI controls passed. The schema-check command exited 0:

```text
PYTHONPATH=src /home/admin/Salience/.venv/bin/python -c 'import json; from pathlib import Path; from jsonschema import Draft202012Validator; Draft202012Validator.check_schema(json.loads(Path("docs/v4/p1-rg0-rg1-external-acceptance.schema.json").read_text(encoding="utf-8")))'
```

The one-route and two-route packages had zero schema errors and each returned `exit 1; structural_status=VALID; disposition=NO_GO; gate_state=UNCHANGED`, preserving local-structure-only semantics. The mixed-case semantic alias package had 23 schema errors and returned `exit 2; structural_status=INVALID; disposition=NO_GO; gate_state=UNCHANGED; SCHEMA_VALIDATION_FAILED`.

```text
/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-p1-canonical-route-controls/one-route.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 1; structural_status=VALID; disposition=NO_GO

/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-p1-canonical-route-controls/two-routes.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 1; structural_status=VALID; disposition=NO_GO

/home/admin/Salience/.venv/bin/python scripts/check-p1-external-acceptance.py --package /tmp/salience-p1-canonical-route-controls/mixed-case-alias.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
exit 2; structural_status=INVALID; disposition=NO_GO; SCHEMA_VALIDATION_FAILED
```

`compileall` and `git diff --check` passed. Generated `__pycache__` directories and temporary CLI fixtures were removed. No ledger, application migration, external client, production state, or release gate was modified.
