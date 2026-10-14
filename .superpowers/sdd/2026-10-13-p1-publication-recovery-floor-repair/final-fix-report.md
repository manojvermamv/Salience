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

### Attempt 4 actual retry recorder evidence and workflow-replay correction

The first attempt-4 detached shell ended before pytest wrote an exit record; it
is excluded. Its log contains only Compose health checks, no strict progress or
candidate worker was created, and the receiver remained empty. The monitored
foreground producer then used exact image
`sha256:c880a41e4a20f1322ad579b0b8d3f8b5ae14fcdf13635458d33cdc8c747e09a0` and
fresh receiver `baf9d9416c24bcdb87e5e4b406851cbb5e1c57ecab8442153862e9efd46a0245`
(run `0cd02a35-70e5-4642-8b4b-5c09c83f4118`). The corrected four-mount check
passed and both actual pending activity retries ran through the exact image:

- `expired_permit_receipt`: worker
  `4a2dd2be7bb4b7d5d42c9f8358d2649de27533c9326aaf0824eaba16967836b9`,
  pre-retry history boundary event 27, recorder run
  `b35eeae0-52fa-4e5f-b3b9-1a64585d8dd7`, worker ID
  `b65dd9f3-3ec3-4d7e-9cb9-4593f52849a5`; event count 0 and typed hold
  `LegacyPublicationRecoveryHeld` in `submit_or_reconcile`.
- `published_during_cancel`: worker
  `410d98092f3ec3faf70d527caabfeb57e04845ff1e37f077091fd76d2db2f580`,
  pre-retry history boundary event 44, recorder run
  `5437d952-8b0b-4dbe-9fc6-8eb112ba914c`, worker ID
  `c9f6e918-23b4-4ff9-9517-0ce277809a17`; event count 0 and typed hold
  `LegacyPublicationRecoveryHeld` in `cancel`.

Both worker logs show activity attempt 2, the exact expected typed hold
diagnostic, and zero operations from each independent per-worker recorder. The
prelaunch/stop/readback records are
`task8-worker-retry-attempt-expired_permit_receipt.json` and
`task8-worker-retry-attempt-published_during_cancel.json` in the candidate
evidence directory. Primary and separate legacy database readbacks matched
their strict hold and liability hashes; the serialized histories and recorder
readback are preserved in
`/tmp/p1-publication-recovery-successor-182c4e4/attempt-4/failed-state-readback.json`
(SHA256 `95f03bc06daeb306a4a540c5fea25dffb2351399ecf9bde941c3f2482967676f`).

The producer later exited 1 after 159.77 seconds in the separate post-failure
workflow replay check. RED was `assert len(new_schedule_ids) == 1`, which saw
zero. The reset helper intentionally resets at the first workflow-task
completion after the durable activity failure; replay therefore consumes the
recorded hold result and adds no activity schedule or start. The following
cross-container test incorrectly expected a second activity execution even
though the first exact-image worker above already exercised both pending
activity retries. I corrected that later replay check to assert no added
schedule/start events, retain the exact original target activity schedule and
start, require the typed hold result, and keep the database and recorder empty.
This preserves the stronger separation between an actual pending activity
retry and workflow replay after its recorded non-retryable failure.

The corrected exact-image replay cases are GREEN: **2 passed, 152 deselected,
15.81s**, exit 0, in
`/tmp/p1-publication-recovery-successor-182c4e4/attempt-4/replay-focused-green.log`
(SHA256 `59f4ca7ccdc27b5348e32b3bdc36d026b59e93b787e3b16df6b60ef0a9d8faea`).
They preserve the original activity event sets, confirm unchanged complete
liabilities, and read back empty recorder runs. Their exact evidence JSON files
are the `e7b4961c...` expired case and `491e9fcc...` cancellation case in the
candidate evidence directory. This focused GREEN validates the correction;
the complete producer still requires a fresh run from new disposable targets.

Attempt-4 exact targets remain preserved pending readback-qualified cleanup:
primary `recovery_floor_active_3f8cbb474778`, legacy
`recovery_floor_legacy_50db37943b9c`, and receiver
`baf9d9416c24bcdb87e5e4b406851cbb5e1c57ecab8442153862e9efd46a0245`. No other
candidate-image container is running. The correction changes tests only; the
frozen application commit, pinned source rows, wheel, installed runtime, image,
and launch-lock contract are unchanged, so no candidate rebuild is required.

### Attempt 4 cleanup and post-cleanup candidate audit

The attempt-4 readback initially recorded pre-refresh progress SHA
`a2c1b8a164299e23c10c233985f51077bdd0c7156ddd874b6132eb77d65c8aa5`; the
producer's final progress refresh subsequently updated the canonical file to
SHA `b948eb9110f76f9ea05831cd36770fe22ab8c871cac5be8be7ebda6c5c68e436`. I
retained the first readback and reran the full database, Temporal history,
recorder, and worker-attempt readback against the refreshed bytes. The current
readback passed and is `attempt-4/failed-state-readback-precleanup-current.json`
(SHA256 `bbd5c4815659bf22d7f3eb22e02cd8f93c1aa96bc24a6ffcdd58d665fcdaa7d8`).
It verifies both exact hold-row hashes, complete 21-key liability snapshot
hashes, expected migration head, both per-worker recorder event counts zero,
and empty shared receiver run.

Readback-qualified cleanup removed only attempt-4 receiver
`baf9d9416c24bcdb87e5e4b406851cbb5e1c57ecab8442153862e9efd46a0245` and dropped
only `recovery_floor_active_3f8cbb474778` (17,585,843 bytes) and
`recovery_floor_legacy_50db37943b9c` (17,143,475 bytes). The exact refreshed
progress bytes are preserved at
`attempt-4/worker-qualification-progress.failed-after-replay.json` with SHA
`b948eb9110f76f9ea05831cd36770fe22ab8c871cac5be8be7ebda6c5c68e436`; the
canonical progress file was then removed. Cleanup command exit was 0 and the
machine-readable record is `attempt-4/cleanup-attempt4.json`. The successor
image itself was preserved.

The post-cleanup invalidation audit passed at `HEAD=767cdcd8945133107429722c21929bcd7635f1ee` with progress absent. The pinned application commit, `HEAD`, and worktree each reconstructed all 211 source-lock rows byte-for-byte: path-set SHA `14bddb59e1d7ad1f3d4fceb6b20e7ae0748848a93e89dc470a7af7b325dc88fe`, content manifest SHA `58f27973abc9b3d34fa9f2eea1738c914659ff84f5151f64fdc493acca1c3005`. Candidate loader passed for image `sha256:c880a41e4a20f1322ad579b0b8d3f8b5ae14fcdf13635458d33cdc8c747e09a0`, wheel SHA `425b7122f6431459a15bc4fe7bbdd169692b152356a0f36c6895e3fd452e3d04`, runtime manifest SHA `3d58423ee9119db38c9a670c726c83692bef126d402e69f2a9ad98e05377b644`, 9,151 installed entries, and zero `.pyc`. This confirms the changed test and reports do not invalidate or require rebuilding the frozen candidate. Audit log and exit are `attempt-4/candidate-invalidation-audit-after-cleanup.log` and `.exit`.

A fresh full Task 8 producer run is still required after the replay assertion correction; no producer pass or final qualification is claimed yet.

### Attempt 5 result and approved test prerequisite restoration

The fresh attempt-5 producer exited 1 after 217.36 seconds in the late A4
hostile-source negative control. The pre-floor checkout required at test line
8199 was missing at
`/tmp/salience-f3-retained-039ddf39cb9b846c205f352e083293fbec6f9fc5`. Exact log
SHA256 is `5a4e8f15177d539e6ca1869cb679d5098a699ac363a810d7281a53018c21e78a`.
Both actual exact-image pending retries had already run and produced the
expected typed holds on activity attempt 2; both independent recorder event
counts and the shared receiver count were zero. The exact full readback
`attempt-5/failed-state-readback.json` is SHA256
`e48dfca45250b87dafe9af2c17ab9f967c53513404c25f077918bee12df701c6`.

Attempt-5 progress SHA `6bd1b88050894831bbeab22a78f958f87fd26e34fb524ada9c7f23a5c6170e52`
was archived after database/Temporal/recorder readback. Cleanup removed only
receiver `cf99188c6d73940dc7914f86db85ac35047c7892674a6557f5b4118b4eb66a90`,
primary DB `recovery_floor_active_07e9b354ec19`, and legacy DB
`recovery_floor_legacy_020927697458`; both database sizes were 17,585,843 and
17,143,475 bytes. It removed canonical progress and preserved the candidate
image. Cleanup exit was 0; JSON evidence SHA256 is
`20fb8613ffde0ec41f1546d8366c40e7e548a65996826f44194bf455bdf85718`.

The controller explicitly authorized recreating the required detached clean
worktree at `/tmp/salience-f3-retained-039ddf39cb9b846c205f352e083293fbec6f9fc5`
at exact commit `039ddf39cb9b846c205f352e083293fbec6f9fc5`. It will be kept only
for the hostile-source controls, verified clean and detached before use, then
removed after dependent qualification with the final worktree list checked.
No tracked test behavior was changed to suppress the missing prerequisite.
Attempt 6 must use fresh targets before Task 8 can be claimed green.

### Attempt 6 Task 8 producer GREEN and disposable-state cleanup

With the exact clean detached PREFLOOR worktree restored, the fresh attempt-6
full Task 8 producer passed (**1 passed in 194.14s**, exit 0). Exact log SHA256
is `929f6de5bf5bf9b6f3b0e42cd48c3e9fae240200c795b5cf91a8b5edaceb1b2a`. The
producer completed both actual exact-image activity retries and embedded A4
hostile-source controls. `expired_permit_receipt` produced the expected typed
hold on `submit_or_reconcile` attempt 2 with independent recorder run
`243c8119-bdda-4241-a708-601c49448755`, worker ID
`cdd47a20-93f1-470d-896b-f884a189065e`, event boundary 27, and zero operations.
`published_during_cancel` produced the expected typed hold on `cancel` attempt 2
with recorder run `e66bea22-9a8f-48b9-8d00-82f2e731c4c4`, worker ID
`8c88c87b-8d2f-4163-953e-adbc36102723`, event boundary 44, and zero operations.
The final readback SHA256 is
`722723b7d6d18d9e37ec84d0945a7486ea9d3fd2e06018ce1dac482bcc29edc4`; it checks
both exact DB hold hashes, complete 21-key liability hashes, histories, empty
worker recorders, and empty shared receiver run `a9ad25f3-3cd9-4e43-af75-92bb22760d4f`.

Progress SHA256 `2ad3f53d70362a14b6f880e3274dae346a686714098225fd1a72b23b89811f8a`
was archived. Cleanup removed only receiver
`0f670640cc9e98ee888e3289a22a34bebaf821bbe8bf6b00e073111bca8cec53`, primary
DB `recovery_floor_active_d5b1b7603b46`, and legacy DB
`recovery_floor_legacy_6c14b37c98cd`; measured sizes were 17,585,843 and
17,143,475 bytes. It removed canonical progress and preserved the exact image.
Cleanup exit 0 is recorded in `attempt-6/cleanup-attempt6.json` (SHA256
`b89c803ad39c24548260716108a53c36cd5f0c4f1af80c79f90748c1df614d7e`).

The detached pre-floor checkout remains in place until standalone exact-image
hostile-startup and physical migration inventory overlays complete. Its
verified clean/detached identity and initial worktree list are in
`attempt-5/prefloor-worktree-verification.json`. After dependent checks I will
remove it and verify only the original `arch` and active recovery worktrees
remain. Full non-live and consolidated gates remain pending.

### Standalone exact-image matrix, PREFLOOR worktree removal, and candidate audit

The retained standalone exact-image/prerequisite command passed **12 tests in
47.26s**, exit 0; the command and output are
`attempt-6/exact-image-matrix.command.txt` and `.log` (log SHA256
`7e62dfdc422898c90de1ba658a06e432f6ac4e5493e66a2285364bf34ce0d317`). It
covers absent/partial prerequisite gate behavior, exact candidate loader,
real empty-0042 denial, `PYTHONPATH`/`PYTHONHOME`/cwd hostile overlays, valid
malicious `.pyc` with a positive execution control, and exact-image physical
extra/missing/changed/down-revision migration inventory overlays.

The exact detached PREFLOOR worktree at
`/tmp/salience-f3-retained-039ddf39cb9b846c205f352e083293fbec6f9fc5` was used
only for the passing full Task 8 producer's existing A4 hostile-source control,
then removed with `git worktree remove`. The remaining worktree list has only
`/home/admin/Salience` on `arch` and this recovery worktree at
`3ae005f673c1688041a5bc1c4c2bd1a13f8fcbd6`; recorded output SHA256 is
`192105008205ea3fe8f64183f54323234b8c92f39c588d2e275111d2f5c37647`.

The final-pre-suite candidate invalidation audit passed with progress absent.
All 211 source rows match at the pinned application commit, current HEAD, and
worktree; path-set digest is
`14bddb59e1d7ad1f3d4fceb6b20e7ae0748848a93e89dc470a7af7b325dc88fe`, content
manifest digest is
`58f27973abc9b3d34fa9f2eea1738c914659ff84f5151f64fdc493acca1c3005`. The
successor wheel/image/runtime remain the pinned identities; runtime verification
reports 9,151 entries and zero `.pyc`. Audit log SHA256 is
`220f2d19d39c042b2feb73440b27a4bb91861a075e2b5fc382f9d87aa6a45453`. The full
non-live suite, final audit hash/inventory refresh, and consolidated docs checks
are still outstanding.
