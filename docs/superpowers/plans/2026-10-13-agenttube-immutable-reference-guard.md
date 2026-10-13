# AgentTube Immutable Reference Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the reference verifier reject an initialized AgentTube checkout unless it is detached at the exact gitlink commit and has no tracked or untracked changes.

**Architecture:** Keep the existing `validate_gitlink(root: Path, manifest: dict) -> list[str]` interface and add initialized-submodule checks in that function only. Temporary tests will create a real Git commit, point the fixture gitlink at that commit, and patch the verifier's `PIN` only inside each test so its commit identity is real without changing the reviewed production pin.

**Tech Stack:** Python standard library (`unittest`, `unittest.mock`, `subprocess`, `tempfile`) and the Git CLI.

**Spec:** `docs/superpowers/specs/2026-10-13-agenttube-reference-and-native-adoption-design.md`

## Global Constraints

- The reference path is `youtube-automation-agent/`, URL `https://github.com/darkzOGx/youtube-automation-agent`, update hint `master`, and reviewed pin `e26792ae00dc1ec2434faf32632896609396dfad`.
- An initialized reference must be detached at the exact superproject gitlink commit and `git status --porcelain --untracked-files=all` must be empty. A mismatch or dirty checkout stops reference comparison until restored or reinitialized at the pin; do not preserve local changes by stash, commit, or patch.
- Do not edit, generate files in, install dependencies in, run, or otherwise modify anything under `youtube-automation-agent/`. All initialized-submodule test repositories and mutations live under `tempfile.TemporaryDirectory()`.
- The verifier accepts an uninitialized submodule and must not invoke its status check when the submodule is uninitialized. Normal CI, packaging, and deployment continue to exclude upstream checkout, installation, imports, runtime, scheduler, and credentials.
- Keep `SALIENCE_EFFECTS_ENABLED=false`, production effects disabled, and publication budget `0`; RG0 and RG1 remain `HELD`; P2–P5 and P7 remain `GATED`; optional P6 remains `DISABLED`; `main` remains `UNMERGED`.
- This plan implements only the immutable reference guard. Any later native adoption must select only a capability or implementation idea shown to be useful and better than Salience's current equivalent. Its own bounded design must record a comparative decision (`keep Salience`, `adapt concept`, `reimplement natively`, or `reject`), generic multi-platform fit, safety-gate compatibility, provenance/licensing review, and bounded acceptance tests. Do not port the upstream repository, architecture, runtime, schemas, or platform-specific assumptions wholesale.
- These tracked files were already modified when this plan was written and must stay outside the implementation commit: `docs/superpowers/specs/2026-10-13-agenttube-reference-and-native-adoption-design.md`, `docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md`, and `docs/v4/IMPLEMENTATION-BLUEPRINT.md`. `docs/v4/agenttube-adoption-evidence.json` and the four hash-bound reports under `docs/research/youtube-agent/` are existing evidence; this guard adds no new evidence records or report edits.

## Review Focus

- An initialized checkout with a modified tracked file or any untracked file is rejected — `test_initialized_submodule_rejects_tracked_change` and `test_initialized_submodule_rejects_untracked_change`.
- A clean checkout attached to a branch at the exact gitlink commit is rejected — `test_initialized_submodule_must_be_detached_at_gitlink`.
- A nonzero `git status` result fails closed even when the checkout's `HEAD` is readable — `test_initialized_submodule_rejects_status_failure`.
- A clean detached checkout at a different, real commit is rejected specifically for its `HEAD` mismatch — `test_initialized_submodule_must_match_gitlink`.
- An uninitialized submodule remains accepted without running `git status` — strengthen `test_gitmodules_and_gitlink_match_the_reviewed_pin` to assert this.

---

### Task 1: Enforce an immutable initialized reference checkout

**Files:**
- Modify: `docs/v4/test_agenttube_reference.py`
- Modify: `docs/v4/verify_agenttube_reference.py` (`validate_gitlink`)

**Interfaces:**
- Consumes: Existing `init_gitlink_repository(root) -> dict`, `manifest_fixture() -> dict`, `_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]`, and `validate_gitlink(root: Path, manifest: dict) -> list[str]`.
- Produces: Test helper `init_initialized_gitlink_repository(root: Path, *, detached: bool = True) -> tuple[dict, str]`, which creates a real temporary submodule commit, updates the parent index gitlink and manifest pin to that SHA, and returns the manifest and SHA. Tests temporarily patch `VERIFIER.PIN` to that SHA with `unittest.mock.patch.object`; the production PIN remains unchanged.

- [ ] **Step 1: Add the real-commit fixture and dirty-worktree regression test**

In `docs/v4/test_agenttube_reference.py`, import `mock` from `unittest`. Keep `init_gitlink_repository(root)` for the uninitialized parent fixture and remove its `initialized_head` branch that writes a fake `.git/HEAD`. Add `init_initialized_gitlink_repository` using the parent fixture, then initialize `youtube-automation-agent/` as a nested Git repository, create and commit a tracked fixture file using per-command temporary author identity, read its SHA with `git rev-parse HEAD`, and replace the parent index's gitlink SHA with that commit. Set `manifest["upstream"]["pin"]` to the same SHA. Leave the nested repository on its initial branch only when `detached=False`; otherwise check out the commit detached.

Add `test_initialized_submodule_rejects_tracked_change` and `test_initialized_submodule_rejects_untracked_change`. Each test uses a fresh temporary repository and first asserts the clean detached fixture passes while `VERIFIER.PIN` is patched to the fixture SHA. The tracked case then changes the committed fixture file; the untracked case creates a new file. Assert the resulting validation contains a cleanliness/dirty-worktree error. The current verifier should accept both dirty cases, so both tests must fail before implementation.

- [ ] **Step 2: Run the dirty-worktree test and observe the expected failure**

Run:

```bash
python3 docs/v4/test_agenttube_reference.py -v AgentTubeReferenceContracts.test_initialized_submodule_rejects_tracked_change
python3 docs/v4/test_agenttube_reference.py -v AgentTubeReferenceContracts.test_initialized_submodule_rejects_untracked_change
```

Expected: both named tests FAIL because `validate_gitlink` returns no error for the changed initialized worktree; each clean baseline assertion passes. Do not proceed if either failure instead comes from fixture creation, gitlink setup, or pin patching.

- [ ] **Step 3: Add branch, status-error, exact-head, and uninitialized-boundary coverage**

In `docs/v4/test_agenttube_reference.py`, add `test_initialized_submodule_must_be_detached_at_gitlink` using `detached=False`; assert its `HEAD` equals the fixture pin and validation reports that the checkout must be detached. Add `test_initialized_submodule_rejects_status_failure`, patching `VERIFIER._git` to delegate all commands except `("status", "--porcelain", "--untracked-files=all")`, for which it returns a `subprocess.CompletedProcess` with a nonzero status and stderr; assert validation reports the status failure.

Replace the fake `.git/HEAD` setup in `test_initialized_submodule_must_match_gitlink` with the real-commit helper. Create a second empty commit in the temporary submodule using per-command test author identity, leave it detached and clean, then assert validation reports that `HEAD` must match the gitlink pin. In `test_gitmodules_and_gitlink_match_the_reviewed_pin`, wrap the uninitialized validation with a spy around `VERIFIER._git` and assert no call uses `status`, while retaining its current acceptance assertion.

- [ ] **Step 4: Run the new branch and command-failure tests and observe the expected failures**

Run: `python3 docs/v4/test_agenttube_reference.py -v AgentTubeReferenceContracts.test_initialized_submodule_must_be_detached_at_gitlink AgentTubeReferenceContracts.test_initialized_submodule_rejects_status_failure`

Expected: FAIL because the current verifier neither checks branch attachment nor runs the status command. The fixture's clean `HEAD` and temporary pin must be valid in both cases.

- [ ] **Step 5: Add the minimal initialized-submodule checks in `validate_gitlink`**

In `docs/v4/verify_agenttube_reference.py`, keep the existing `.git` presence test and exact `rev-parse HEAD` comparison. For an initialized submodule only, call `_git(worktree, "symbolic-ref", "--quiet", "HEAD")`; reject return code `0` as branch-attached, accept `1` as detached, and report any other return code as a command failure. Also call `_git(worktree, "status", "--porcelain", "--untracked-files=all")`; reject a nonzero return code and reject any nonempty stdout. Preserve acceptance when `.git` is absent, and do not change the manifest, repository pin, runtime-boundary checks, or other verifier behavior.

- [ ] **Step 6: Run the focused verifier suite and all requested documentation checks**

Run:

```bash
python3 docs/v4/test_agenttube_reference.py
python3 docs/v4/verify_agenttube_reference.py
python3 docs/v4/test_checks.py
python3 docs/v4/check.py --self-test
git diff --check
```

Expected: all 17 AgentTube reference tests pass; the reference verifier and documentation checks report PASS; `git diff --check` exits 0. Confirm the real `youtube-automation-agent/` checkout remains unchanged and clean. If a documentation/evidence check reports drift in the pre-existing modified files listed above, record that result and leave those files and evidence locks untouched.

- [ ] **Step 7: Create exactly one implementation commit**

Stage only the verifier and its focused tests, then create one commit:

```bash
git add docs/v4/test_agenttube_reference.py docs/v4/verify_agenttube_reference.py
git commit -m "fix: enforce immutable AgentTube reference checkout"
```

Do not include the pre-existing documentation changes, plan file, ignored activity log, evidence manifest, research reports, or any path under `youtube-automation-agent/` in this implementation commit.
