# P1 Publication Recovery Floor Repair Design

**Date:** 2026-10-13
**Status:** Approved 0043 repair design; included in the 2026-10-13 specification-set review; implementation, exact-candidate requalification, final candidate review/meta-review, and exact-head checks remain open.

## Purpose and relationship to the earlier design

This specification closes the recovery-floor defects recorded in the Task 11 independent review and accepted by the main-agent meta-review. It defines one cohesive repair: a complete immutable 21-key liability baseline on each new recovery hold, a trustable Python launch path, a working wheel-only image migration command, and an authenticated exact migration inventory.

This document supersedes only the incomplete publication-recovery floor and evidence portions of [the 2026-10-06 P1 design](2026-10-06-p1-publication-recovery-hold-design.md). That design remains the authority for the original hold reasons, publication activity ordering, inspection contract, parallel-team status correction, and other approved behavior. Migration 0042 and its rows remain historical inputs and are not revised by this specification. For the floor repair described here, this specification is authoritative.

The reviewed inputs are `task-11-main-meta-review.md`, `task-11-fix-options.md`, and `task-11-gpt61-independent-review.md` under `.superpowers/sdd/2026-10-06-p1-publication-recovery-hold/`, plus Task 10 and Task 11 of `docs/superpowers/plans/2026-10-06-p1-publication-recovery-hold.md`.

## Verified failure modes

| Finding | Verified defect | Required repair |
|---|---|---|
| I1 | Migration 0042 and the rollback verifier retain and compare 16 keys. Later changes to `remote_publication_receipts`, `job_checkpoints`, `audit_events`, and `provenance_records` are invisible. `output` is already present inside the full `job` row, but the v1 contract must also name it explicitly. | Add a separately versioned, complete 21-key baseline to new holds; require replay and rollback verification to compare it. |
| I2 | An unrecorded executable `.pyc` can run in place of pinned source, and worker launches can create new caches. `-B` alone does not reject an existing cache. | Reject every `.pyc` under the authenticated runtime root before application imports, use `-B`, and keep that root read-only through `exec`. |
| I3 | The supported control launcher starts under plain Python. `PYTHONPATH`, `PYTHONHOME`, `sitecustomize`, and current-directory imports can run before its environment scrub or digest checks. | Start the external control process in isolated, no-site, no-bytecode mode and test the actual supported subprocess command. |
| I4 | The wheel-only image copies migrations into site-packages but has no installed Alembic config. Compose runs `alembic upgrade head` from `/app`; the local relative `script_location=migrations` does not resolve to the copied migration tree. | Install an Alembic config beside migrations, use an explicit `-c` path, and execute a real upgrade in a disposable database from the built image. |
| I5 | The launcher pins 0042 but the migration graph parser accepts any valid-shaped extra file in the copied directory. That file can make an unknown database revision appear to be a known descendant. | Authenticate the complete migration/config file inventory and require exact set equality before parsing or using revision metadata. |
| M1 | The ignored Task 10 report records `docs/v4/documentation-status-audit.json` as `7f06f404226b346873755d76c6cffdcc7ddfb7ecad79d5de57f770b44010ebcf`; the exact committed bytes hash to `65cb0ac34344f7d8931ceec32b214797c05e73a82dba4b64108184c9e9394836`. | Correct this report-only digest during the final evidence pass and refresh any references to that report. It does not change runtime behavior or authorize rewriting historical evidence. |

## Scope and unchanged release boundaries

The repair changes the existing hold representation and its trust gate only. It does not change either hold reason, the publication activity ordering, the signed inspection endpoint, or the no-action meaning of a hold. It introduces no companion baseline table, new application role, hold resolution path, worker service, queue, or production policy.

The disposable fixture remains effects-disabled and its publication budget remains exactly zero. Production effects, production routing, production rollback, and production-wide revocation remain unqualified and disabled. RG0 and full RG1 remain `HELD`; P2–P7 remain gated; P6 remains disabled; `main` remains unmerged. AgentTube remains a pinned reference-only artifact; this repair does not adopt, execute, or promote it.

## V1 scope and future-platform boundary

`salience.publication-liability.v1` and its complete 21-key projection are closed evidence for only the existing `v4_legacy_publication_recovery_holds` legacy fixture path and its two hold reasons. “Complete” means complete for this exact projection and its bound relations; v1 is not a universal provider/platform liability schema or adapter contract. Future native publication paths require phase-owned, versioned evidence against their authoritative canonical records. Do not add platform-specific branches to v1. Bind platform or account identity only from authoritative canonical records, and never infer historical platform, account, or liability facts from current state. A future path reusing this legacy hold mechanism requires a new snapshot version and additive migration before enablement; a separate canonical hold path defines its own evidence contract in its phase.

A hold is safety evidence only. It cannot advance or reopen a cycle, select another stage or provider, invoke an adapter, become an observation or outcome, or trigger learning, an experiment, or a release.

## Migration 0043 and immutable baseline schema

Create `migrations/versions/0043_complete_publication_recovery_liabilities.py` with revision `0043_complete_publication_recovery_liabilities` and `down_revision = "0042_publication_recovery_holds"`. Do not edit migration 0042. Migration 0043 is additive, does not update or delete any row, and does not backfill a v1 baseline from current state. Every pre-existing row receives SQL `NULL` in all three new columns and every old value, ID, fingerprint, and row count remains unchanged.

Add these nullable columns to `public.v4_legacy_publication_recovery_holds`:

| Column | PostgreSQL type | Nullability | Meaning |
|---|---|---|---|
| `liability_snapshot_version` | `text` | nullable | Exact tag `salience.publication-liability.v1`. |
| `liability_snapshot_v1` | `jsonb` | nullable | The complete immutable 21-key snapshot below. |
| `liability_fingerprint_v1` | `text` | nullable | Lowercase SHA-256 hex digest of PostgreSQL's canonical `liability_snapshot_v1::text`. |

Retain 0042's `liability_snapshot`, `liability_fingerprint`, original 16-key trigger, table, foreign keys, unique operation identity, reason/state constraints, `immutable_legacy_publication_recovery_hold` trigger, and public privilege revocation unchanged. The old and new snapshots coexist on new rows so the old 0042 contract remains intact and independently enforced.

Add the following named constraints:

* `ck_v4_legacy_publication_recovery_holds_v1_all_or_none`: all three v1 columns are `NULL`, or all three are non-`NULL`. This permits preserved 0042 rows and prevents partial v1 records.
* `ck_v4_legacy_publication_recovery_holds_v1_version`: a non-`NULL` tag equals exactly `salience.publication-liability.v1`.
* `ck_v4_legacy_publication_recovery_holds_v1_snapshot_keys`: a non-`NULL` value is a JSON object with exactly the 21 names below: all names are present and subtracting that name array leaves `{}`.
* `ck_v4_legacy_publication_recovery_holds_v1_fingerprint`: a non-`NULL` value matches `^[0-9a-f]{64}$`.

Add migration-owned function `public.v4_guard_legacy_publication_recovery_hold_v1()` and `BEFORE INSERT` trigger `validate_legacy_publication_recovery_hold_v1` on the existing table. The function is `SECURITY DEFINER` with fixed `search_path = pg_catalog, public`. Before reading `NEW`, it requires `TG_RELID` to equal `public.v4_legacy_publication_recovery_holds` and requires `TG_TABLE_SCHEMA = 'public'` and `TG_TABLE_NAME = 'v4_legacy_publication_recovery_holds'`; any other trigger target raises an exception. Revoke `EXECUTE` from `PUBLIC`, so callers cannot attach or directly invoke this definer function on caller-owned relations. The migration owner can still create the hold-table trigger, which runs the guard as its owner. It requires the exact v1 tag, all three non-`NULL` fields, exact 21-key shape, a fingerprint equal to `encode(public.digest(NEW.liability_snapshot_v1::text, 'sha256'), 'hex')`, and equality between the submitted snapshot and a fresh canonical projection of the same bound dispatch, receipt, attempt, job, and related rows. It uses the same identity, reason, scope, request, receipt, and publication predicates as the unchanged 0042 guard. A missing canonical row, changed binding, wrong reason, stale snapshot, extra key, missing key, wrong tag, or wrong digest raises an exception and aborts the insert. The 0042 guard continues to authenticate the original 16-key projection on that same insert.

The migration owner remains the table/function owner. The trigger is the only write-time path for v1 validation. `PUBLIC` continues to have no privilege on the hold table. The concrete fixture runtime principal created by provisioning (the existing `fixture_runtime_<12 hex>` `NOLOGIN` role) keeps only `USAGE` on schema `public` and table-level `SELECT` and `INSERT` on the hold table. It receives no `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`, or schema `CREATE` privilege. No role or extra grant is created by 0043. The existing update/delete rejection trigger also makes all three new columns immutable.

The mandated 46-character revision identifier `0043_complete_publication_recovery_liabilities` exceeds Alembic's default `alembic_version.version_num varchar(32)`. Before Alembic records 0043, widen that migration-metadata column to `varchar(64)`. Keep it at `varchar(64)` after downgrade so Alembic can record and transition from this revision; do not shrink it back to 32. This permanent exception affects only Alembic's revision metadata and has no application-table, application-data, ownership, grant, or application-authority effect.

The 0043 downgrade sets the existing three-second `lock_timeout`, takes `SHARE ROW EXCLUSIVE` on the hold table, and refuses if any row has any non-`NULL` v1 column. On refusal, Alembic stays at 0043 and no object or row changes. If no v1 baseline exists, it drops only `validate_legacy_publication_recovery_hold_v1`, `v4_guard_legacy_publication_recovery_hold_v1()`, the four named constraints, and the three added columns, then leaves all 0042 objects and rows intact. The `alembic_version.version_num varchar(64)` metadata width is the permanent exception described above. The unchanged 0042 downgrade still refuses to remove the hold table when any hold row exists.

## Canonical v1 snapshot

Build one `jsonb_build_object` containing these exact keys in this contractual order:

1. `dispatch`
2. `context`
3. `cycle`
4. `job`
5. `permit_claim`
6. `permit`
7. `fixture_receipt`
8. `fixture_progress`
9. `publication_request`
10. `publication_plan`
11. `publication_attempt`
12. `external_effect`
13. `budget_reservation`
14. `output`
15. `remote_publication_receipts`
16. `publication_status_events`
17. `cycle_events`
18. `cycle_outbox`
19. `job_checkpoints`
20. `audit_events`
21. `provenance_records`

The first 13 keys retain 0042's row projections: `to_jsonb` of the bound row for each named base relation, with `external_effect` and `budget_reservation` represented as JSON `null` when their left-joined row is absent. `output` is `to_jsonb(job.output_payload)`, including JSON `null` if the SQL value is null. Each collection is a JSON array and uses `COALESCE(..., '[]'::jsonb)`, never SQL `NULL`.

The four collections omitted from 0042 are filtered and ordered as follows. Include every row matching the stated key; do not add state, timestamp, tenant, or “latest row” filters.

| Key | Relation and filter | Total deterministic order |
|---|---|---|
| `remote_publication_receipts` | `public.remote_publication_receipts` where `publication_attempt_id = publication_attempt_row.id` | `created_at, id` |
| `job_checkpoints` | `public.job_checkpoints` where `job_id = job.id` | `sequence_no, id` |
| `audit_events` | `public.audit_events` where `job_id = job.id` | `created_at, sequence_no, id` |
| `provenance_records` | `public.provenance_records` where `job_id = job.id` | `created_at, id` |

The existing arrays retain their current deterministic orders: publication status events by `sequence_no, id`; cycle events by `created_at, id`; and cycle outbox rows by `sequence, id`. The database trigger, recording function, replay comparison, and offline verifier use the same projection and filters. The fingerprint is computed from PostgreSQL JSONB canonical text, not Python dictionary serialization or an order-sensitive JSON encoder. Exact key membership is checked separately from the digest.

## Recording, replay, verification, and schema gate

The application computes the complete projection once for the candidate hold. In one PostgreSQL transaction and one `INSERT`, it stores the unchanged 0042 values plus `liability_snapshot_version`, `liability_snapshot_v1`, and `liability_fingerprint_v1`. The 0042 and 0043 `BEFORE INSERT` triggers independently recompute and compare their respective projections. There is no follow-up update or second-table write. Existing advisory locking, identity row locks, unique `operation_id`, exact conflict behavior, and committed-return semantics remain.

Exact replay first requires the stored v1 tag, snapshot, and fingerprint. A 0042-era row whose v1 fields are `NULL` raises `PublicationRecoveryConflictError`; it is never upgraded in place and current database state is never used to fabricate its historical baseline. For a v1 row, replay validates the 21-key set and fingerprint, recomputes the current projection, and compares the entire old and new evidence plus all existing identity and reason fields. Any mismatch denies replay.

`verify_publication_recovery_rollback` remains read-only and repeatable-read with the existing three-second statement timeout. It requires exactly one distinct operation ID for each of `expired_permit_receipt` and `published_during_cancel`, both in the requested workspace. For each row it requires a complete v1 tag, exact 21-key set, valid fingerprint, and equality with a fresh projection. Missing rows, `NULL` v1 columns, duplicate or swapped IDs, wrong reasons, malformed evidence, or any changed canonical liability deny verification. It continues to compare the unchanged 0042 evidence as well.

The new application has a hard schema minimum of revision `0043_complete_publication_recovery_liabilities`. It rejects a database at 0042, including an empty hold table, before worker registration. It also rejects an earlier, unknown, branched, ambiguous, or unauthenticated migration state. Existing 0042 rows can still be inspected as held history, but a `NULL` v1 row cannot be replayed or used by the rollback verifier. `current` startup requires the schema floor and exact artifact pin; `rollback-restart` additionally requires the complete both-reason verifier to pass before `exec`. The in-process checks in the deployable and fixture worker remain defense in depth; the independent control launcher is the gate that must deny before the worker process registers.

## Python startup and bytecode trust

The actual supported deployable command in both the Docker default and Compose is:

```text
/usr/local/bin/python -I -B -S /run/salience-control/launch-v4-publication-worker.py --mode current --worker-kind deployable
```

The fixture command has the same interpreter flags and fixed launcher path, with `--worker-kind legacy-fixture`. The Docker qualification harness invokes that same command and runs the worker container with `--read-only`. The launcher, launch lock, and independent lock-digest control mounts remain read-only.

`-I` ignores `PYTHONPATH`, `PYTHONHOME`, user-site configuration, and the working directory as import sources; `-S` prevents `site`, `.pth` execution, and `sitecustomize` before the launcher starts; `-B` prevents Python from writing bytecode. Before the first Salience or third-party import, the launcher uses standard-library modules only to authenticate the fixed interpreter, control launcher, independent lock digest, lock bytes, retained wheel, installed `RECORD`, every recorded file, source/blob manifest, runtime import paths, closed worker entrypoint, and runtime root. It then enumerates the runtime root and rejects any `.pyc` entry, including adjacent caches, `__pycache__` entries, and symlinked `.pyc` paths. `-B` is still required because cache rejection alone does not prevent new writes.

The qualified image installs application and third-party wheels directly under the one authenticated runtime root, `/usr/local/lib/python3.13/site-packages`. `-S` is compatible with those imports because it omits automatic site initialization, not the standard library, extension loader, or an explicitly added path. The authenticated preflight and worker child also start with isolated no-bytecode no-site flags and a standard-library-only `-c` bootstrap. Only after receiving the root from the already authenticated lock does that bootstrap prepend the exact fixed runtime root to `sys.path`; it then loads the pinned preflight or closed worker module. It does not run `-m` before adding that path and does not depend on `.pth` files or `sitecustomize`. The built-image qualification must import the pinned application and its installed third-party dependencies under this bootstrap before the candidate is accepted.

The runtime root stays read-only from the initial cache scan through worker `exec`. Compose sets `read_only: true`; the actual qualification container uses `--read-only`. Any required temporary storage is an explicit `/tmp` tmpfs and cannot cover or shadow the runtime root or control mount. The launcher does not rely on `PYTHONDONTWRITEBYTECODE`, which isolated mode ignores, and the application does not receive a writable mount at the authenticated root.

Process-level negative controls invoke the real supported launcher command, not `launcher.main()` in a pre-imported test process:

* Put a marker-writing `sitecustomize.py` and shadow modules on hostile `PYTHONPATH`, set hostile `PYTHONHOME`, and launch from a working directory containing shadow modules. Assert the marker is absent, no candidate module is imported, and the worker is not registered.
* Place a valid malicious timestamp/size `.pyc` beside unchanged, correctly pinned source and unchanged `RECORD`; its code writes a marker if executed. Assert the launcher rejects before application import, the marker is absent, and the `.pyc` remains present. A disposable positive control proves the fixture would load the cache without the rejection gate.
* Run the actual qualified image read-only and assert no `.pyc` appears during preflight or worker execution and the authenticated root remains unchanged through `exec`.

## Installed-image Alembic configuration

Keep the wheel-only installation: install exactly the retained `SALIENCE_WHEEL_FILE` artifact and do not run `pip install .`. Continue copying the reviewed migrations into `/usr/local/lib/python3.13/site-packages/migrations`. Change the tracked `alembic.ini` to `script_location = %(here)s/migrations` and copy it to `/usr/local/lib/python3.13/site-packages/alembic.ini` beside that directory. Compose's migration command is exactly:

```text
alembic -c /usr/local/lib/python3.13/site-packages/alembic.ini upgrade head
```

The image qualification first runs Alembic configuration/history resolution from that installed image, then runs the actual migration service command against a newly created disposable PostgreSQL database and observes revision 0043 at head. It records the image digest, wheel digest, command, database migration output, and final revision. A static Compose string assertion is not migration evidence. No production database is used.

## Complete authenticated migration inventory and lineage

Use the existing independent publication-worker launch lock's `source_files` records; do not add a second manifest format. Each source record binds its path, Git blob ID, and SHA-256, and the canonical source manifest digest is covered by the lock whose SHA-256 is mounted separately from the candidate. Include `alembic.ini`, `migrations/__init__.py`, `migrations/env.py`, `migrations/versions/__init__.py`, every revision module from 0001 through 0043, and every later migration support file Alembic loads. The 0042 bytes remain unchanged; 0043 is a new pin.

Before reading any `revision` or `down_revision`, derive the expected revision-module set only from the authenticated manifest, enumerate the actual `migrations/versions` regular `.py` file set, and require exact set equality. Reject any extra or missing file, symlink/path escape, or content whose Git blob or SHA-256 differs. Then parse revision metadata from the same verified bytes, without importing or executing revision modules. Validate unique revision IDs, exactly one root and one head, one parent per non-root revision, every parent present, no cycles, and a valid ancestry chain from `0042_publication_recovery_holds` to `0043_complete_publication_recovery_liabilities` and the database's current revision. A valid-shaped extra descendant is rejected at the set-equality check before its metadata can influence acceptance.

The launcher authenticates `alembic.ini`, `env.py`, and the full version inventory before the compatibility code reads Alembic state or uses the graph. The compatibility check accepts only revision 0043 or an authenticated descendant of 0043 for which the exact candidate has been qualified. An older 0042 candidate cannot run against 0043: its expected migration inventory is incomplete and its schema floor is insufficient.

Every future migration changes the authenticated inventory and graph. It requires a new application candidate, a fresh wheel and installed-image build, updated source/RECORD/import/lock pins and external lock digest, exact-set and graph negatives, the real disposable image migration smoke, both-reason artifact rehearsal, focused and full checks, and clean independent review/meta-review. A syntactically valid descendant is not accepted merely because it is a descendant; the exact updated inventory and candidate must be independently qualified.

## Acceptance and qualification evidence

The implementation is accepted only with all of the following evidence on one candidate:

1. **0043 preservation and guards:** migrate a disposable database from 0042 to 0043 with existing 0042 rows; prove row counts, IDs, and all pre-existing columns are unchanged and every v1 column is `NULL`. Prove the four named constraints and trigger reject a missing key, extra key, bad tag, bad fingerprint, mismatched current snapshot, or a new insert with null v1 evidence. Prove the runtime role can `SELECT`/`INSERT`, cannot mutate history or create triggers, and `PUBLIC` has no table privilege. Prove the 0043 downgrade refuses after any v1 baseline and leaves 0043 untouched; with no v1 baseline it removes only 0043 additions and preserves every 0042 row. Prove the existing populated 0042 downgrade still refuses.
2. **Independent liability denials:** for each of `remote_publication_receipts`, `job_checkpoints`, `audit_events`, and `provenance_records`, mutate only that collection while all old 16-key values remain identical. Require exact replay and the offline verifier to deny; require `rollback-restart` launch to deny before worker registration. Include both append and update forms where the schema permits them.
3. **Legacy and schema-floor denials:** preserve a pre-0043 row with all v1 fields `NULL` and prove exact replay, offline verification, and a `rollback-restart` launch using that row deny without updating it or registering a worker. With the database at 0042, prove the new application rejects before registration even if the hold table is empty. Prove an authenticated 0043 row can be verified and a changed/missing v1 key cannot.
4. **Concurrency and crash recovery:** for each of the two reasons, race exact insertions and prove one immutable row and one stable hold ID. For each reason, hard-exit after the hold transaction commits but before the activity returns; exact replay must return the same ID. Capture canonical 21-key snapshots before and after and prove every liability is unchanged.
5. **Hostile startup and caches:** run the real command-level hostile `PYTHONPATH`/`PYTHONHOME`/working-directory cases and the malicious `.pyc` case described above. Prove denial precedes candidate import and worker registration; preserve the positive cache control and assert no cache creation in a read-only actual worker run.
6. **Migration inventory:** independently prove denial for a valid extra descendant after 0042, a removed version file, and changed revision/down-revision bytes. Each case fails exact inventory/hash checks before graph acceptance. A complete authentic graph passes.
7. **Image migration:** build the wheel-only image from the retained wheel, resolve its installed Alembic config/history, and execute `alembic -c /usr/local/lib/python3.13/site-packages/alembic.ini upgrade head` against a disposable database. Verify revision 0043 and retain command and image evidence.
8. **Actual artifact rehearsal:** create authentic retained v1 holds for both reasons, run the offline verifier, drain the fixture worker, then invoke the externally controlled launcher in `rollback-restart` mode with both exact operation IDs. The actual wheel-derived read-only image must return the typed held outcome for each and make zero outbound adapter invocations, regardless of method name or platform, as asserted by a method/platform-neutral adapter-operation recorder. The recorder must cover mutations; submission, upload, and create operations; readback/status; cancellation; and all other remote operations. Retain exactly one stable hold per operation and preserve byte-identical full liabilities. Run the verifier again and obtain the same two IDs. Negative artifact, lock, interpreter, runtime-root, migration-inventory, and database-liability cases must deny before registration.
9. **Full non-live and documentation checks:** run the Task 8 focused command and Task 10 per-reason/migration/floor suite; then run Task 9's complete non-live suite (`pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs`), `.venv/bin/python docs/v4/test_checks.py`, `.venv/bin/python docs/v4/check.py --self-test`, `.venv/bin/python docs/v4/check.py`, every `tests/scripts/*.sh`, JSON parsing for every changed JSON file, the qualified Mermaid rendering command, `.venv/bin/python -m pip check`, `.venv/bin/python -m compileall -q src tests scripts docs/v4`, and `git diff --check`. Record actual exits, totals, durations, hashes, image/wheel IDs, and disposable-database boundaries; do not reuse prior candidate results as results for this candidate.

Qualification evidence belongs under the ignored `artifacts/v4-next-p1/agents/` tree and is summarized only after exact commands and artifacts are available. The M1 report hash correction is reconciled in that evidence pass; no source lock or evidence lock is updated while this specification alone is being authored.

## Candidate invalidation, review, and integration

There is one application candidate, one retained wheel, one installed `RECORD`, one image, one source/import manifest, one launch lock, and one independent lock digest for this repair. Any change to runtime source, migration source or inventory, Alembic config, Docker image inputs, launcher/bootstrap, runtime root, pinned import path, or launch-lock contract invalidates the candidate and requires a new complete wheel/image/manifest qualification. Any test or documentation change after review invalidates the corresponding final verification, review, and exact-head CI claim; rerun the affected checks and obtain review of the final exact candidate. Do not combine evidence from different candidate SHAs.

After the complete acceptance run, obtain a read-only GPT-6.1-Sol review of the exact candidate and artifacts, then a GPT-5.6-Sol meta-review validating each finding. Any accepted implementation change produces a new candidate and repeats build, artifact rehearsal, tests, review, and meta-review. Push the feature branch only after both reviews are clean on the final unchanged candidate. Verify the required `verify` and `p0-regression` CI jobs against that exact pushed `headSha`; stale-head or partial CI does not qualify the candidate. No push or merge to `main` is part of this design.

## Migration and operational recovery limits

Migration 0043 is forward-compatible with retained 0042 history but does not make a 0042 row replayable. Historical NULL-v1 rows remain immutable, visible, and non-resumable; an operator cannot infer the missing old baseline from today's liability state. A rollback attempt with either legacy NULL-v1 reason fails closed. Do not delete or rewrite holds to pass verification.

The supported worker floor is forward-only at schema 0043: a 0042 application or any unknown, unpinned, unrelated-history, pre-floor, source-mismatched, artifact-mismatched, or migration-inventory-mismatched worker cannot start. Rollback means restarting only an independently reviewed and qualified application whose exact artifact understands 0043, after successful both-reason verification. If that verification or the external trust gate fails, keep the fixture worker stopped and preserve the holds and database state. Do not downgrade the schema to make an older binary start.

The 0043 downgrade rule protects all newly complete baselines. Even when it is allowed because no v1 baseline exists, it is an administrative schema reversal only and does not authorize starting this new application at 0042; the new application rejects that revision. The existing 0042 populated downgrade remains prohibited. This specification defines no hold-retirement, production rollback, or production recovery mechanism; those require a separate reviewed design.
