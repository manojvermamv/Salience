# Task 8 — exact-candidate qualification report

**Status: DONE.** The final candidate was built from `APP_COMMIT=386d4bc7cc5acb794858d3c3c14400a0659a83ef`. The reviewable tracked status/lock commit is `03ae9e2ac8665338529012f9482d6836f10e1413` (`docs: repin Task 8 status to 386d exact artifact`). The worktree is clean on `work/v4-p1-publication-recovery`; no push was made. The final independent corrected-publication rereview, GPT-5.6 meta-review, push, and exact-head CI remain controller-owned gates.

The final acceptance-coverage self-review was performed before implementation and recorded in `implementation-report.md` under “Task 8 pre-implementation acceptance coverage self-review.” It mapped every repair acceptance item to the existing or new Task 8 evidence path. This report records the resulting exact-candidate outcomes without transferring historical counts.

## Candidate and pin identity

The retained wheel SHA-256 is `da88befdc0ff607839fcdb9c2d7b8fa7f32b2b80eda7965a653dfd3bc3107f16`; the ignored Compose-default copy at `artifacts/salience.whl` has identical bytes. Both candidate image references inspect to the same image ID: `sha256:46d252a4abdbbebdf81cf02782c4b7668ff5ffe5221f823a6e55e272436301a7` (`salience-p1-floor-386d4bc-worker:latest` and `salience-p1-publication-recovery-0043-worker:latest`). The image was created at `2026-10-14T06:23:12Z`; its digest is the Docker content-addressed image ID.

The final lock SHA-256 is `0139f60fd82dbb6a57719db8739edf1cc786a94032e24b93e75d5a59eb050302`; the source manifest is `cf3e0d3da75c92a45c94671ddaab6f764a1a5823f17a5fa8a2a508fbbc578820` (211 source entries and 9 pinned runtime imports). The installed distribution RECORD has 172 entries, SHA-256 `be343894b0b561f17cc825caf96927e4dbb08e5e0a42cbfebe7bb50f64e073f3`, and all entries matched. The exact extracted interpreter SHA-256 is `73b68fc2dac176af16098180a2c98a03ee9f1f1b02db25d42d44d782e79e2330`. The authenticated runtime manifest contains 9,151 entries, no `.pyc`, and SHA-256 `eda06374310c4882f8e61e8ee9fc21c8e208fc598724d81fca6b03b1772147d1`. The three-file control bundle matched the tracked lock, launcher, and independent digest byte-for-byte.

Two approved build-input details are recorded here. First, extraction preserved the image’s nested `/usr/local/lib/python3.13/site-packages` topology and the installed `/usr/local/bin/content` target, so RECORD resolution used the authenticated image layout. The builder used that nested path as `--runtime-root` and the exact interpreter bytes extracted from the image as `--interpreter-source`; the emitted in-container executable remains `/usr/local/bin/python`. Second, the Compose image query returned no worker container ID because the build had not created a service container. The build output and exact tagged image were verified with `docker image inspect`; the fixed `salience-p1-floor-386d4bc-worker:latest` alias and Task 8 alias both resolve to the image ID above. The strict RECORD assertion was retained.

The temporary clean builder worktree and retained hostile-source worktree were removed after their checks. Final `git worktree list` contains only `/home/admin/Salience` at `039ddf39cb9b846c205f352e083293fbec6f9fc5` (`arch`) and this recovery worktree. The `youtube-automation-agent` submodule is clean and detached at `e26792ae00dc1ec2434faf32632896609396dfad`.

## Exact rehearsal state and readback

The final strict producer/readback run bound primary database `recovery_floor_active_2635d1ad84c3`, legacy database `recovery_floor_legacy_c80e0bfd516d`, Temporal target `172.20.0.3:7233`, and recorder URL `http://172.20.0.4:8081`. Workspace `d2aa634b-a2f1-44a2-9ca1-294c49edcd30` has the two primary holds and exact operations:

| Reason | Operation ID | Hold ID |
| --- | --- | --- |
| `expired_permit_receipt` | `71a17a01-734e-4a32-b6da-419b91c42244` | `e48e39aa-6dc0-41e8-9d82-e7bf47f2cb32` |
| `published_during_cancel` | `26f70c2e-fa4b-42fa-9374-b9888dc9b528` | `c8042712-c0b8-4ca5-b965-74138a08d3fa` |

The independent post-suite readback matched each persisted hold row and complete 21-key liability snapshot to strict progress. The hashes were `086d30f1a92ddaa1f5b20cef0235d94325e08d1a70d1d02232c9427daedd4359` and `8d6d27856403e68349fbc3c807548a5faf055fca7f96537692399a368d5d7b52` for the hold rows, and `8d6a2c57028331df8201baeefe705ef33d438b779ae52566e4a836d6fd4119bb` and `73dbf57e909f6a26d190709abaf9c2ad5316b9ee002587e0257a80f5fde00a32` for their liability snapshots. The verifier passed three times (before rehearsal, after current-mode rehearsal, and after rollback-restart), each exit code 0, with the same two reason/operation/hold bindings.

The two primary histories were read back immediately before cleanup and matched the deterministic protobuf digest, task queue, and event boundary in strict progress: `expired_permit_receipt` ended at event 36 with SHA-256 `661aee14cbf49516b702a98b8f75318553dbf490a9fcba45a8dff6091af99b13`; `published_during_cancel` ended at event 53 with SHA-256 `86a9a9b2697295c086b24f0eba8ff976ed8a531726c9e14f329e3f685c3ea556`.

The distinct legacy database was built from 0042 and upgraded to 0043. Its legacy operation `f9acb86b-362b-40c8-800d-f48059cd04ff` retained NULL v1 snapshot fields and was denied in preflight before worker registration. Its v1 companion remained separate. Both legacy liability graphs and hold rows matched their strict hashes; the NULL-v1 row’s three snapshot columns were `[null, null, null]`, its outbox rows remained pending and undispatched, and its inbox count was zero. This is preflight-only evidence and makes no claim of legacy NULL-v1 cancellation replay.

For the Task 7 cross-container recorder runs, the expired-receipt replay used receiver run ID `ea430996-cf6c-4e1b-808a-65a5181b4702`; the published-during-cancel replay used `2ae986ef-591d-471e-ac92-93f6a746e65c`. Both began and ended with empty run-specific receiver state, returned the typed `LegacyPublicationRecoveryHeld`, and created zero new activity schedule/start events. The cross-container positive control also passed and recorded its expected operations. The existing positive-control helper does not persist its generated run ID; its passing result is included in the no-skip 153-test output. The dedicated qualification receiver run `3b0f40da-91ce-4abd-a88d-0b52f3950871` read back zero events.

The actual rollback launcher inspected `Path=/usr/local/bin/python` and the exact argv beginning `[-I, -B, -S, /run/salience-control/launch-v4-publication-worker.py, --mode, rollback-restart, --worker-kind, legacy-fixture, ...]`, followed by the recorded workspace and both operation IDs above. The two worker-container read-only-rootfs assertions passed. The recorder-role guard found only the exact Task 8 recorder container among running containers descended from the candidate image; no other candidate worker remained running.

## Qualification commands and results

All Task 8 selectors below were run without skips against the final image/control bundle. Their full output and detached-wrapper exit records are in `/tmp/p1-publication-recovery-0043/`.

- Persistent exact-artifact producer: `1 passed, 149 deselected in 187.61s`; wrapper exit 0. The producer performed independent database, history, receiver, image-ID, runtime-manifest, and control-bundle readbacks before publishing strict progress.
- Real-image rehearsal selector (`floor_worker_executes_both_retained_reasons_and_second_verifier_passes`, `rollback_restart_launcher_verifies_both_holds_before_exec`, and read-only/no-pyc): `3 passed, 147 deselected in 197.42s`.
- Exact inventory/pin/omitted-collection/legacy-NULL-v1 matrix: `17 passed, 133 deselected in 102.26s`. This covers six full-inventory cases, six pin mutations, four omitted-liability-collection cases, and legacy NULL-v1 denial.
- Focused application suite: `202 passed, 3 warnings in 806.56s`; the warnings are the existing Alembic `path_separator` deprecation.
- Final retained Task 10 reason/migration/floor command, including all tests in the Task 8 floor file and the three named retained hold/migration selectors: `153 passed in 385.60s`, exit 0, no skips.
- Complete non-live command used `-p no:cacheprovider`, the normal shared test database, and unset `P1_TASK8_PERSIST_FIXTURES`, `TEST_LEGACY_NULL_DATABASE_URL`, and every `TEST_ADAPTER_RECORDER_*` variable: `789 passed, 17 skipped, 3 warnings in 1570.07s`. The 16 candidate-progress skips were expected because strict progress was archived during this aggregate; the remaining monolithic gate skipped because its recorder URL/run ID were unset. The three warnings are the same existing Alembic deprecation. No historical 614-test or earlier-candidate result was transferred to this candidate.
- Exact-image installed Alembic migration on a newly created disposable database: exit 0, head `0043_complete_publication_recovery_liabilities`, `alembic_version.version_num` `character varying(64)`, role set unchanged, no non-version tables populated. Disposable database `task8_migration_386d4bc_0042bf2570a0` was dropped and absence verified. Evidence is `task8-386d4bc-installed-image-migration.json` (SHA-256 `f6ea894df09dc34ab3327a93375f0a804a15b63af16b1ed33b5a206a052ab66c`).
- `docs/v4/test_checks.py`: 15 tests passed. `docs/v4/check.py --self-test`: PASS, 75 requirements, 365 evidence files, 360 historical inventory files, 97 Markdown files, 4 HTML files, 6 negative controls, no errors. `docs/v4/check.py`: PASS, same counts and no errors.
- All four `tests/scripts/*.sh` checks passed: browser documentation, Phase 7–8 documentation, Phase 9 documentation, and browser evidence.
- `docs/v4/verify_diagrams.py`: all six diagrams passed with Mermaid 12.0.0; output hashes are in `artifacts/v4-next-p1/qualification-diagrams/results.json` (SHA-256 `6ab6bcbe4a8d1d0af7521e51fbf8d76836f012e2d67974f37c7d9c202784f0ae`). `/usr/bin/chromium` was temporarily linked to the already-installed Playwright Chrome-for-Testing headless shell (`151.0.7922.34`) because the expected executable path was absent; the symlink was removed after diagram verification. Mermaid was installed only under `/tmp` with scripts disabled.
- `pip check`: “No broken requirements found.” `compileall -q src tests scripts docs/v4`: exit 0. All six JSON files changed between `APP_COMMIT` and final tracked HEAD parsed with `python -m json.tool`. `git diff --check 386d4bc..HEAD`: exit 0. Final worktree status is clean.

## Cleanup, interruptions, and historical evidence

After all six conditional Task 4 cases, four Task 6 cases, Task 7 recorder replays, and final readbacks completed, the final primary and legacy databases were dropped and verified absent. Recorder container `c6fbaa1119007674012cf1a8948f97a9db3e74579bbc558de6ef3ee699166da0` (`task8-adapter-recorder-386d4bc-a9d25136`) was stopped and removed; its final inspect/logs and cleanup result are archived. The canonical `worker-qualification-progress.json` was archived and removed after cleanup so no active progress file points at destroyed databases. Final readback JSON SHA-256 is `fa99c1793a81d621a706498341fc818bc42612951969a2a8f5c9c8dcfae9e77f`; archived progress SHA-256 is `1028dfdff81dfe131e8f3ebdd2df35d233c5263bc26eca3ee2eecbeeaf218b2c`; final `worker-qualification.json` SHA-256 is `0ed827b6c1800cc72b2b356579555b0f8aeb75f3bf6d5d1ff1603ac7e5df9fbf`.

An earlier retained-suite attempt was not counted: `/tmp` reached 100% while pytest was writing its cache/session summary, leaving the output and cache JSON truncated at 4,096 bytes. Its incomplete fixture state, progress file, complete candidate database dumps, exact table readback, and hashes are preserved under `artifacts/v4-next-p1/agents/p1-publication-recovery-0043/task8-386d4bc-retained-interruption-archive/`. The affected interrupted fixture database had one runnable cycle and pending start message, no hold, and no accepted publication liability. It was dropped after archive and absence verification. The final detached rerun from fresh targets passed with about 2 GiB of `/tmp` available; no source correction was made for this environmental interruption.

Two read-only diagnostic attempts also failed before their corrected reruns: a host readback URL typo after an image migration that itself exited 0, and a readback SQL alias omission followed by an operation-ID mix-up. They changed no database state; the errors and corrected final readback are archived. An earlier SIGTERM/143 run and its exact changed-row evidence are preserved separately; it is not counted as qualification evidence.

The M1 addendum remains ignored report-only evidence at `artifacts/v4-next-p1/agents/p1-publication-recovery-0043/m1-report-digest-correction.json`, SHA-256 `f11f93c3090fc6571590e6484cc2e9076649dec7f5a8d9609a1d5ef8e8d4e062`; it distinguishes the prior reported digest `7f06f404226b346873755d76c6cffdcc7ddfb7ecad79d5de57f770b44010ebcf` from the corrected committed-byte digest `65cb0ac34344f7d8931ceec32b214797c05e73a82dba4b64108184c9e9394836`. The required searches found no dependent current-report references. Historical evidence and counts were not rewritten.

Production effects remain disabled, fixture publication budget is zero, RG0 and full RG1 remain HELD, P2–P7 remain gated, P6 remains disabled, AgentTube remains untouched/reference-only, and `main` remains unmerged. This report and the ignored artifact evidence are ready for the controller’s independent review sequence.

## Review fix round 1/5 — active qualification handoffs (2026-10-14)

The review found that active prose still named `973d347b55a8d69ead695216777c1c4bc57d5812` and described exact-artifact qualification as open. The final exact source is `386d4bc7cc5acb794858d3c3c14400a0659a83ef`, and Task 8 local exact-artifact qualification is complete. I updated the current checkpoint, qualification/CI rows, and resume instructions in `docs/v4/progress.md`; the corresponding current checkpoint, execution handoff, publication-recovery row, repository-controls row, and resume summary in `docs/v4/IMPLEMENTATION-BLUEPRINT.md`; and the current status header in the approved repair design. Historical Task 0–7 records, candidate counts, the saved review finding, and archived review diffs were preserved. HELD/disabled/gated states remain unchanged. No runtime/test source, candidate artifact, image pin, JSON, or content-lock file changed; no JSON digest pointer required recomputation.

The focused fail-first handoff assertion was RED before edits: **6/6** checks failed across progress and blueprint because the obsolete SHA was present, the final SHA was absent, and completion was not stated. The GREEN assertion passed **12/12** checks across the three active documents: old SHA absent, exact `386d` SHA present, local exact-artifact qualification explicitly complete, and GPT-5.6 meta-review/exact-head CI retained as open controller gates. The active status documents contain no stale `973d` reference; the superseded value appears here only to record the finding, and remains in the preserved review finding and archived diff.

Verification after the edits:

- `python3 docs/v4/test_checks.py` — **15 passed**.
- `python3 docs/v4/check.py --self-test` — **PASS**, 75 requirements, 365 evidence files, 360 inventory files, 97 Markdown files, 4 HTML files, 6 negative controls, zero errors.
- `python3 docs/v4/check.py` — **PASS**, same consolidated document/inventory totals, zero errors.
- Current status/lock JSON parse check — **6 files parsed** (`documentation-status-audit.json`, `evidence-lock.json`, `p0-release.json`, `p1-qualification-matrix.json`, `publication-recovery-hold-evidence.json`, and the deployment launch lock); no JSON changed.
- Active-handoff assertions — **12/12 passed**; `git diff --check` — **PASS**.

Tracked fix commit: `b69b040c6061046d9f92f9d391d69c6c0c36418a` (`docs: reconcile exact 0043 qualification handoffs`), touching only the three Markdown files named above. The source commit, wheel/image/lock pins, historical counts, and qualification evidence remain unchanged. No push was made; final corrected-publication review/meta-review, feature-branch push, and exact-head CI remain controller-owned.

A final standalone runtime-manifest recheck first failed because the shell did not bind the shared interpreter to this worktree’s `src`; rerunning with `PYTHONPATH="$PWD/src:$PWD/tests/integration"` verified the wheel, control bundle, lock, 9,151-entry runtime manifest, and zero-`.pyc` image state above.

## Successor candidate qualification addendum (in progress)

The historical DONE result above remains the archived Task 8 result for candidate
`386d4bc`. The approved final-fix wave built successor application candidate
`182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f`, image
`sha256:c880a41e4a20f1322ad579b0b8d3f8b5ae14fcdf13635458d33cdc8c747e09a0`, and
wheel `425b7122f6431459a15bc4fe7bbdd169692b152356a0f36c6895e3fd452e3d04`.
Its actual Task 8 retry-recorder producer has not passed yet; no successor
qualification is claimed in this interim addendum.

The first fresh successor producer attempt reached the exact worker container,
but exited 1 in 33.27 seconds at the harness argv assertion before any activity
retry. The actual command includes `-I -B -S -c`, the test recorder launch shim,
and then `/run/salience-control/launch-v4-publication-worker.py`; the assertion
still expected the launcher immediately after `-S`. Exact log and exit evidence
are `/tmp/p1-publication-recovery-successor-182c4e4/attempt-2/task8-producer.log`
and `.exit`. Failed progress SHA-256 is
`206bb975db5853022c6837ecc6ac333a7de2dc233af6b70fde49222b551e54ad`.

The failed attempt's primary holds and complete liability snapshots matched
progress. Its two workflow histories gained only natural timeout events (28 and
45) after their previously captured boundaries; no activity start was observed.
Cleanup archived progress, verified and removed only the attempt-2 recorder
container, dropped only its exact primary and legacy databases, and removed the
canonical progress file. The evidence bundle is under
`/tmp/p1-publication-recovery-successor-182c4e4/attempt-2/`. The recorder's
per-worker run IDs were not persisted before the assertion, so this failed
attempt does not claim recorder evidence for either actual retry.

The harness assertion was corrected to include the exact recorder launch shim.
Two focused worker-launch/shim tests passed (`2 passed in 0.63s`), and the exact
candidate loader passed with progress absent. The test-only correction does not
change wheel/image inputs. A fresh exact-candidate Task 8 producer and the
consolidated qualification remain required; final status will be appended here
with the resulting IDs, hashes, test counts, and cleanup evidence.
