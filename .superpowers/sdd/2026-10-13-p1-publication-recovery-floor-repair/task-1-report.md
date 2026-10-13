# Task 1 — Migration 0043 report

Status: complete.

## Changes

Added `migrations/versions/0043_complete_publication_recovery_liabilities.py` with `revision = "0043_complete_publication_recovery_liabilities"` and `down_revision = "0042_publication_recovery_holds"`. It adds nullable `liability_snapshot_version`, `liability_snapshot_v1`, and `liability_fingerprint_v1` columns, the four named constraints, and a `BEFORE INSERT` validator implemented as `SECURITY DEFINER` with fixed `search_path = pg_catalog, public`.

The validator preserves the 0042 binding, scope, identity, receipt, request, publication, and reason predicates. Its `jsonb_build_object` projection has the exact 21 keys in contractual order. The seven collections use the specified relation, filter, and total ordering, and the fingerprint comparison is `encode(public.digest(NEW.liability_snapshot_v1::text, 'sha256'), 'hex')`. The existing 0042 function, trigger, table contract, and immutable update/delete trigger remain unchanged.

The downgrade sets `lock_timeout` to three seconds, takes `SHARE ROW EXCLUSIVE`, and checks all three v1 columns for any non-NULL value before dropping a trigger, function, constraint, or column. The legacy-only branch removes only the 0043 additions and keeps 0042 rows. No data backfill or row update/delete and no role or grant were added.

The mandated revision identifier exceeds Alembic’s default `alembic_version.version_num varchar(32)`. The migration widens that metadata column to `varchar(64)` before Alembic records 0043. It remains 64 characters after downgrade so Alembic can complete the revision transition. The hold table and its 0042 contract are unaffected by this metadata change.

## RED / GREEN evidence

The worktree does not contain `.venv/bin/python`; the initial requested command therefore could not start. I used the repository environment at `/home/admin/Salience/.venv/bin/python` for both test runs.

RED command:

```text
/home/admin/Salience/.venv/bin/python -m pytest -q tests/integration/test_v4_migrations.py tests/integration/test_v4_publication_recovery_holds.py -k '0043_upgrade_preserves_populated_0042_rows_and_contract or 0043_insert_guard_rejects_invalid_v1_projection or 0043_downgrade_'
```

RED output: `9 failed, 14 deselected in 72.40s`. All nine selected cases failed while attempting the 0043 upgrade with `Can't locate revision identified by '0043_complete_publication_recovery_liabilities'`, confirming the tests reached the missing-revision behavior.

GREEN command:

```text
/home/admin/Salience/.venv/bin/python -m pytest -q tests/integration/test_v4_migrations.py tests/integration/test_v4_publication_recovery_holds.py -k '0043_upgrade_preserves_populated_0042_rows_and_contract or 0043_insert_guard_rejects_invalid_v1_projection or 0043_downgrade_'
```

GREEN output: `9 passed, 14 deselected in 77.65s`.

## Schema and behavior checks

- Upgraded a populated 0042 database and compared every legacy row field, count, old column definition, table owner, 0042 guard function, and 0042 triggers before and after. All three new columns were NULL on the preserved row.
- Checked fixture role `NOLOGIN`, schema `USAGE` without `CREATE`, table `SELECT`/`INSERT` only, no PUBLIC table privileges, and unchanged role count. Real `UPDATE` and `DELETE` attempts against a preserved row were rejected as immutable.
- Checked the exact three nullable column types and all four named constraints. The snapshot constraint requires an object with both ordered 21-name arrays: `?&` requires every key and JSONB subtraction requires no extras.
- Checked the trigger function’s 21-key projection order, row projections, all collection filters and deterministic orders, JSON-array `COALESCE`, canonical JSONB text digest, `SECURITY DEFINER`, fixed search path, and trigger timing.
- Performed real rejected INSERTs for all six parameter IDs: `missing-key`, `extra-key`, `bad-tag`, `bad-fingerprint`, `current-row-mismatch`, and `null-v1`. Each case asserted no hold row was added.
- Tested downgrade lock timeout, populated-v1 refusal with unchanged revision/schema/row, legacy-only downgrade preserving 0042 row values, and the existing populated 0042 downgrade refusal.

## Paths

- `migrations/versions/0043_complete_publication_recovery_liabilities.py`
- `tests/integration/test_v4_migrations.py`
- `tests/integration/test_v4_publication_recovery_holds.py` (adds the reusable valid 0043 INSERT fixture helper)
- `.superpowers/sdd/2026-10-13-p1-publication-recovery-floor-repair/task-1-report.md`

The migration `0042_legacy_publication_recovery_holds.py` was not edited; its tracked diff is empty.

## Commit and concerns

Commit: this report is included in the single task commit; its SHA is returned in the task completion message to avoid a self-referential SHA inside the committed report.

The focused migration selector passed. The broader publication-recovery application tests were not run in this migration-only task; application call sites still need the follow-up work that supplies the new v1 columns on writes.
