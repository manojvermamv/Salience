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
