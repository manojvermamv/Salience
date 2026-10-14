# Final publication recovery fix wave

This report records the bounded final-review fixes and the exact qualification
evidence produced after the approved review findings. Production effects remain
disabled; fixture publication budget is zero; RG0 and full RG1 remain HELD;
P2–P7 remain gated; P6 remains disabled; main is unmerged.

## Candidate invalidation ruling before the fresh Task 8 retry

The approved design's “Candidate invalidation, review, and integration” rule
requires a new wheel/image and complete candidate qualification after changes to
runtime source, migration source or inventory, Alembic configuration, Docker
image inputs, launcher/bootstrap, runtime root, pinned import path, or launch-lock
contract. A test or documentation change invalidates the affected final
verification, review, and exact-head CI claim. The harness-only commit
`9ecd7e9a9703464d32d123442de6f531cb207622` changes only
`tests/integration/test_v4_publication_recovery_floor.py` and
`tests/fixtures/v4_publication_adapter_operation_recorder.py`; it does not alter
any application/image input. Therefore the exact application candidate frozen
at `182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f` remains valid, while affected
qualification must be rerun and the controller must review the final head.

Evidence and commands:

- `git diff --name-status 182c4e4..9ecd7e9` listed only the two test harness
  files above. `git merge-base --is-ancestor 182c4e4 9ecd7e9` succeeded.
- A Python audit rebuilt the source pin rows from each commit's tracked tree and
  compared them to the launch lock. Both commits have the same sorted source
  path set (211 entries), exact path-set SHA256
  `14bddb59e1d7ad1f3d4fceb6b20e7ae0748848a93e89dc470a7af7b325dc88fe`, and
  exact canonical source-manifest SHA256
  `58f27973abc9b3d34fa9f2eea1738c914659ff84f5151f64fdc493acca1c3005`.
  Reconstructed source rows were byte-for-byte equal to the launch-lock rows at
  both commits.
- `.venv/bin/python` loaded
  `tests/fixtures/v4_publication_candidate_prerequisites.py` and called
  `load_candidate_state(...)` against the canonical candidate directory, tracked
  lock and launcher, and `/tmp/p1-publication-recovery-0043/runtime-prefix`.
  The loader passed its Docker image inspection, wheel hash, control-bundle,
  and complete extracted-runtime manifest checks. It reported application
  commit `182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f`, image
  `salience-p1-floor-182c4e4-worker:latest` / `sha256:c880a41e4a20f1322ad579b0b8d3f8b5ae14fcdf13635458d33cdc8c747e09a0`,
  wheel SHA256 `425b7122f6431459a15bc4fe7bbdd169692b152356a0f36c6895e3fd452e3d04`,
  runtime manifest SHA256
  `3d58423ee9119db38c9a670c726c83692bef126d402e69f2a9ad98e05377b644`,
  9,151 runtime entries and zero `.pyc` entries.
- The failed first Task 8 producer attempt was a test harness failure, not
  candidate invalidation or environmental failure: the recorder proxy did not
  expose the wrapped fixture adapter's `submit_count`, which caused the retry
  activity to fail before the expected held result. RED evidence is
  `/tmp/p1-publication-recovery-successor-182c4e4/attempt-1/tdd-recorder-proxy.red.log`
  (one expected failing assertion); GREEN evidence is the adjacent
  `tdd-recorder-proxy.green.log` (two tests passed). The test-only fix is in
  `9ecd7e9`.

The earlier exact-image and migration evidence remains applicable because this
commit did not change the candidate image or those tests; the final consolidated
verification and the actual-retry recorder producer are rerun after this commit.

Reproducible audit command: `cd /tmp/salience-v4-p1-recovery-20261006 &&
.venv/bin/python /tmp/p1-publication-recovery-successor-182c4e4/candidate-invalidation-audit.py`.
It exited `0`; full output and exit record are preserved at
`/tmp/p1-publication-recovery-successor-182c4e4/candidate-invalidation-audit.log`
and `.exit`.

## Attempt 2 readback, cleanup, and argv correction

The exact Task 8 producer was rerun against the unchanged 182c4e4 candidate with
fresh persistent primary/legacy targets and a fresh receiver. The command was:

```sh
P1_TASK8_PERSIST_FIXTURES=1 \
TEST_ADAPTER_RECORDER_URL=http://172.20.0.4:8081 \
TEST_ADAPTER_RECORDER_RUN_ID=cd1ac4a1-e105-4301-b475-ecfcefe80be7 \
.venv/bin/python -m pytest -p no:cacheprovider -q -rs \
  tests/integration/test_v4_publication_recovery_floor.py::test_floor_worker_executes_both_retained_reasons_and_second_verifier_passes
```

It exited 1 after 33.27 seconds at the existing launcher-argv assertion. The
failure diff showed `inspected["args"][3] == "-c"`, while the assertion expected
the fixed launcher at that index. The test-only recorder harness intentionally
starts Python with `-I -B -S -c <authenticated launch shim> <fixed launcher>`;
no activity retry was reached. The exact pytest log, exit, command wrapper,
recorder container inspect, and attempt progress are retained under
`/tmp/p1-publication-recovery-successor-182c4e4/attempt-2/`. The failed progress
SHA-256 is
`206bb975db5853022c6837ecc6ac333a7de2dc233af6b70fde49222b551e54ad`.

Before cleanup, the primary readback matched both strict hold-row and complete
liability hashes. The primary database was
`recovery_floor_active_615907838dd6`; the distinct legacy database was
`recovery_floor_legacy_7cceab689607`, at migration head
`0043_complete_publication_recovery_liabilities` with two fixture holds. Neither
workflow had an activity start or failure event. During the readback-to-cleanup
interval both histories naturally recorded only `WORKFLOW_EXECUTION_TIMED_OUT`
(event 28 for `expired_permit_receipt`, event 45 for
`published_during_cancel`); no workflow was terminated by cleanup. The exact
history snapshots are in `cleanup-preflight-refresh.json` and
`cleanup-attempt2.json`. The receiver's top-level run had zero events, and the
per-worker run IDs were not yet written to progress because the assertion failed
first; consequently this attempt is not evidence that the worker recorder saw
zero operations.

Cleanup verified the receiver was the sole running container from the exact
candidate image, had a read-only root and a single read-only receiver fixture
mount, then stopped and removed only container
`6dbb05068179d7ad309b24782eca4f521b78080dfe3ad48cbc98c1a495fc4b94`. It dropped
only the two databases listed above after verifying their exact names and saved
pre-drop sizes (17,577,651 and 17,143,475 bytes). It confirmed the canonical
progress file was byte-identical to the archived failed progress and removed
the canonical file. Cleanup output, script, and exit record are retained as
`cleanup-attempt2-state.*`; progress archive remains at
`attempt-2/worker-qualification-progress.failed.json` with the matching hash.

The argv assertion now includes `-c` and the exact
`worker_harness._ADAPTER_RECORDER_LAUNCH_SHIM` before the fixed launcher and
preserves all existing mode, worker kind, workspace, and hold-operation
arguments. RED is the actual-image attempt above. The focused shim and worker
command tests are GREEN: `2 passed in 0.63s` using
`test_start_rollback_worker_installs_recorder_on_the_actual_adapter_path` and
`test_adapter_recorder_launch_shim_wraps_authenticated_launcher_exec` (exit 0).
The exact candidate loader was then run with no progress file; it passed and
reported image `sha256:c880a41e4a20f1322ad579b0b8d3f8b5ae14fcdf13635458d33cdc8c747e09a0`,
wheel `425b7122f6431459a15bc4fe7bbdd169692b152356a0f36c6895e3fd452e3d04`,
and runtime manifest `3d58423ee9119db38c9a670c726c83692bef126d402e69f2a9ad98e05377b644`.
This harness-only correction leaves the proven candidate invalidation ruling
unchanged; the actual retry producer and all final checks must still be rerun.

## Attempt 3 readback and exact mount correction

The argv correction passed in the next actual-image run. Attempt 3 then exited 1
in 34.21 seconds at the adjacent mount assertion: the real container had the
three fixed control mounts plus the read-only recorder fixture mount
`/run/task7-adapter-operation-recorder.py`. The assertion expected only the
three control mounts. RED is preserved in
`/tmp/p1-publication-recovery-successor-182c4e4/attempt-3/task8-producer.log`
(exit 1; SHA256
`3e95b85aaf1ff20aae207eb2b6c6c461bfff644d8ea8a04b5703cc1453f99e7b`). The
worker was stopped by the test's `finally` path before `handle.result()`; this
attempt did not reach or establish either actual retry recorder result.

The archived strict progress SHA256 is
`c234b17c50581749b3a96557a22f3672b9a8a9eac65d9542a07920cca4c77200`. Primary
fixture database `recovery_floor_active_3bf3a996b8b9` was at 0043 with two
holds; both persisted hold rows and complete liability snapshots matched the
progress hashes. Legacy database `recovery_floor_legacy_19963e283fd9` was also
at 0043 with two holds; its outbox readback showed three pending messages with
zero attempts and no receipts. The DB readback is
`failed-state-readback-databases.json` (SHA256
`fc00c18616ddc9a17978b9c18f152dffa93ec5168e848889b3a39ac208a813bb`).

The histories ended at event 27 and 44 before the attempted worker start. Their
only subsequent events were natural workflow timeouts, at events 28 and 45;
there were no activity-start events after the pre-retry boundaries. The exact
workflow/run IDs, full deterministic history event bytes, and hashes are in
`failed-state-readback-histories.json` (SHA256
`5c8db2cd1be51bc41b1e4db7c3c815361dd00817a8c403170fa5323b36ce5875`). The
worker log was empty. The receiver's qualification run was empty, but the old
test did not persist its generated per-worker recorder run IDs before the mount
assertion failed, so no per-worker zero-operation claim is made from this
attempt.

Cleanup verified the receiver was the sole live container from image
`sha256:c880a41e4a20f1322ad579b0b8d3f8b5ae14fcdf13635458d33cdc8c747e09a0`,
then removed only container
`51b343a5fa84b1bd06485bd9db78f746a66aa0bca1446d348826d5e2981c05be`, dropped
only the two exact attempt-3 fixture databases above after pre-drop readback,
and archived then removed canonical progress. The progress archive remains in
`attempt-3/worker-qualification-progress.failed.json`; cleanup JSON SHA256 is
`eeb730b1a9471574ae0088c3cf49581e071074e3fb961e2a5f3cf3aaa63fa76c`.

The next test-only correction adds the recorder fixture to the exact read-only
mount assertion and writes a small per-reason attempt record before worker
launch, including the workflow boundary and generated recorder IDs. The
`finally` path updates it with container inspection, stop result, worker-log
hash, and receiver readback. This ensures a future harness failure still leaves
the recorder run ID needed for an independent readback.

### Candidate invalidation recheck after the argv correction

The ruling still does not require a wheel/image rebuild. The approved
application-commit pin remains `182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f`; it is
an explicit frozen application identity and is not inferred from later `HEAD`.
The new changes since that pin are tests and reports, including the argv and
mount assertions; no application source, migration, Alembic, Docker input,
launcher, bootstrap, or lock contract changed. A fresh audit against the
current `HEAD=e49131da50d792556b30d36dc88be4ae41145f9a` and the live worktree
reconstructed all 211 tracked source rows from the lock. The path-set digest
remains `14bddb59e1d7ad1f3d4fceb6b20e7ae0748848a93e89dc470a7af7b325dc88fe` and
the canonical source manifest remains
`58f27973abc9b3d34fa9f2eea1738c914659ff84f5151f64fdc493acca1c3005`; source
rows matched byte-for-byte for the pinned application commit, current `HEAD`,
and current worktree. The lock points to the same wheel SHA256
`425b7122f6431459a15bc4fe7bbdd169692b152356a0f36c6895e3fd452e3d04`, while the
candidate loader again passed Docker image inspection and all installed-runtime
checks with progress absent: image `sha256:c880a41e4a20f1322ad579b0b8d3f8b5ae14fcdf13635458d33cdc8c747e09a0`, runtime
manifest `3d58423ee9119db38c9a670c726c83692bef126d402e69f2a9ad98e05377b644`,
9,151 entries, zero `.pyc`. The exact audit script and output are
`attempt-3/candidate-invalidation-audit-current.py` and `.log` (exit 0). No
candidate rebuild is required for the test-only mount/evidence correction; the
Task 8 producer and affected final checks still must be rerun.

The exact mount expectation and per-worker prelaunch evidence are now in the
test harness. The two focused launch-shim tests passed after the correction:
`test_start_rollback_worker_installs_recorder_on_the_actual_adapter_path` and
`test_adapter_recorder_launch_shim_wraps_authenticated_launcher_exec` (**2
passed, 152 deselected, 0.15s**, exit 0), captured in
`attempt-3/post-mount-fix-focused.log` and `.exit`. The changed integration test
also passed `.venv/bin/python -m py_compile`; `git diff --check` passed. One
initial focused invocation while correcting the test failed during collection
from an indentation error and is retained as
`attempt-3/argv-and-mount-focused-green.log` (exit 4); the test source was
corrected before this passing invocation. The exact-image mount assertion and
actual retry-recorder assertions remain pending the next fresh producer run.
