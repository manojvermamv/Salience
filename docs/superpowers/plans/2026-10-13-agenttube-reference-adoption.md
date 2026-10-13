# AgentTube Reference Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the approved AgentTube commit as a pinned, reference-only Git submodule, preserve the reviewed research as tracked evidence, and make the repository check that neither normal CI nor Salience runtime consumes it.

**Architecture:** Keep the upstream tree outside Salience's application, package, and credential boundaries. Record the Gitlink, branch update hint, immutable report copies, and adoption state in repository metadata and docs; validate those contracts with a local read-only verifier and documentation CI. This slice makes no application, database, or production behavior change.

**Tech Stack:** Git submodules; Python 3.13 `unittest`; JSON evidence; Markdown documentation; GitHub Actions documentation workflow.

**Spec:** `docs/superpowers/specs/2026-10-13-agenttube-reference-and-native-adoption-design.md`

## Global Constraints

- Submodule path: `youtube-automation-agent/`; upstream URL: `https://github.com/darkzOGx/youtube-automation-agent`.
- `.gitmodules` records `branch = master` as an update hint; the Gitlink pins `e26792ae00dc1ec2434faf32632896609396dfad`.
- The submodule is reference-only: do not import it, package it, install its dependencies, start it, or give it credentials.
- Normal Salience CI does not initialize the submodule or install its package graph.
- `SALIENCE_EFFECTS_ENABLED=false`, production effects disabled, and publication budget zero remain in force.
- RG0 and RG1 remain HELD. P2–P7 remain gated; optional P6 remains disabled; `main` remains unmerged.
- Continue the current P1 queue from Task 10 and the open obligations; do not replay completed P0/P1 work or credit this reference slice toward P1 release.
- Do not change application code, runtime configuration, migrations, database state, provider dependencies, production settings, active schedules, or release ledgers.
- Preserve the worktree's pre-existing changes. Never run `git clean`, `git reset`, `git stash`, broad `git add`, or deinitialize/delete a submodule except for the exact reviewed rollback and clean temporary-worktree cleanup steps below.
- At execution, use `superpowers:using-git-worktrees` to create a fresh implementation worktree from the reviewed plan-bearing revision; keep this planning worktree and its unrelated modifications untouched.
- Do not implement or plan native P2–P7 behavior here. Each native phase requires its own plan after its existing gate permits it.

## Review Focus

- **The upstream `master` tip has advanced:** verify that the fixed pin is reachable from the fetched upstream history; never move the pin to match the new tip. `test_manifest_keeps_approved_pin_when_master_tip_advances` and the network provenance procedure cover this.
- **The requested object cannot be fetched or is not in the reviewed history:** stop before editing `.gitmodules`; do not substitute a tag or newer commit. `test_pin_not_reachable_from_master_is_rejected` must fail closed.
- **An audit copy or manifest path is missing, altered, or escapes the repository:** reject it instead of accepting a partial report set. `test_research_report_hashes_match_tracked_copies`, `test_report_path_must_stay_inside_repository`, and `test_changed_report_copy_is_rejected` cover this.
- **A CI workflow initializes the submodule or installs its package graph:** fail the reference-boundary check; `test_normal_ci_does_not_initialize_or_install_submodule` covers checkout options and commands.
- **The manifest or status text suggests a release:** keep effects disabled, budget zero, RG0/RG1 HELD, P2–P7 gated, P6 disabled, and main unmerged. `test_evidence_keeps_all_current_release_gates` covers this.

---

## File responsibilities

- `.gitmodules` — upstream URL and `master` update hint for one named submodule.
- `youtube-automation-agent/` — mode-`160000` Gitlink at the approved commit; never a runtime source tree.
- `docs/research/youtube-agent/architecture-audit.md`, `ops-quality-audit.md`, `salience-gap-audit.md`, `orchestrator-synthesis.md` — byte-for-byte tracked copies of the existing research reports, preserving their source hashes.
- `docs/v4/agenttube-adoption-evidence.json` — machine-readable provenance for the Gitlink, report hashes, upstream non-live verification, Salience evidence boundary, and unchanged release gates. It is adoption evidence, not a release qualification artifact.
- `docs/v4/verify_agenttube_reference.py` — read-only validator for the evidence manifest, report hashes, `.gitmodules`, staged Gitlink, CI configuration, and runtime/package boundary.
- `docs/v4/test_agenttube_reference.py` — focused positive and negative checks for the validator.
- `.github/workflows/v4-documentation.yml` — invoke the focused validator tests and verifier in the existing documentation job; do not enable recursive checkout.
- `docs/v4/IMPLEMENTATION-BLUEPRINT.md` — link the approved design, this bounded slice, and the P1-before-native-phase boundary without changing phase requirements.
- `docs/v4/progress.md` — add a dated reference-adoption checkpoint while preserving the current P1 handoff, historical results, and gate statuses.
- `docs/superpowers/plans/2026-10-13-agenttube-reference-adoption.md` — this reviewed implementation plan. The planning report under `.superpowers/sdd/` is a session artifact and must not enter the adoption commit.

The research snapshot and provenance manifest are intentionally under `docs/`, where the existing documentation link/structure checks inspect them. Do not add report copies to the application source lock, alter V4 requirement IDs, modify `p0-release.json`, or claim upstream tests as Salience evidence.

## Task 1: Pin and qualify the reference-only research snapshot

**Files:**
- Create: `docs/research/youtube-agent/architecture-audit.md`
- Create: `docs/research/youtube-agent/ops-quality-audit.md`
- Create: `docs/research/youtube-agent/salience-gap-audit.md`
- Create: `docs/research/youtube-agent/orchestrator-synthesis.md`
- Create: `docs/v4/agenttube-adoption-evidence.json`
- Create: `docs/v4/verify_agenttube_reference.py`
- Create: `docs/v4/test_agenttube_reference.py`
- Create: `.gitmodules`
- Create: `youtube-automation-agent/` Gitlink
- Modify: `.github/workflows/v4-documentation.yml`
- Modify: `docs/v4/IMPLEMENTATION-BLUEPRINT.md`
- Modify: `docs/v4/progress.md`

**Interfaces:**
- `validate_manifest(manifest: dict, root: Path) -> list[str]` validates the exact upstream URL, branch hint, pin, expected report set, repository-relative paths, hashes, and release-state invariants.
- `validate_gitlink(root: Path, manifest: dict) -> list[str]` reads `.gitmodules` and the index entry from `git ls-files --stage`; it requires one mode-`160000` entry at the approved SHA. A submodule worktree may be absent/uninitialized in CI; if initialized, its `HEAD` must equal the pin.
- `validate_reference_boundary(root: Path) -> list[str]` rejects normal CI submodule initialization and application/package references that would import, install, package, start, or credential the upstream tree.
- `verify(root: Path = ROOT) -> list[str]` combines those checks. The command-line entry point prints `PASS` or `FAIL` plus errors and returns nonzero on any error. The verifier never installs, initializes, or executes the submodule.

- [ ] **Step 1: Capture the shared worktree baseline and report hashes**

Before implementation, capture the shared planning worktree status and preserve its report sources. At plan creation, the observed superproject HEAD was `7a6dc4a939dbf5d17503fc71b96babcf311fab11`; it descends from the research-inspected `e7a24a38ef920da3af071a51a288b859db9aaef1`. The V4 progress/blueprint bind the historical 116 focused / 614 full non-live results to application candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4`. These three SHAs describe distinct evidence scopes; do not call `e7a24a3` or `7878208` the actual execution HEAD. The observed dirty baseline also included a modification to `docs/superpowers/plans/2026-10-06-p1-publication-recovery-hold.md`, a modified approved-design status line, untracked `.superpowers/`, no `.gitmodules`, and no `youtube-automation-agent/` path. Re-check current status, HEAD, remotes, and active V4 records before adoption rather than assuming the planning snapshot is still current. Use `superpowers:using-git-worktrees` to create a clean implementation worktree from the reviewed plan-bearing revision. Copy only the four named reports from the shared worktree into the implementation worktree after the red test step; never move or stage `.superpowers/` wholesale.

In the shared worktree, capture its absolute path, status, and source hashes without staging or altering unrelated files. Persist the path before creating the implementation worktree; Step 6 must read the research sources from that absolute path because they are untracked and absent from the clean implementation worktree.

```bash
shared_worktree="$(pwd -P)"
test ! -e /tmp/agenttube-adoption-shared-worktree.path
test ! -e /tmp/agenttube-adoption-commit.sha
printf '%s\n' "$shared_worktree" > /tmp/agenttube-adoption-shared-worktree.path
git status --short --branch
git rev-parse HEAD
git remote -v
git diff --cached --name-only
git diff -- docs/superpowers/plans/2026-10-06-p1-publication-recovery-hold.md
git diff -- docs/superpowers/specs/2026-10-13-agenttube-reference-and-native-adoption-design.md
git ls-files --others --exclude-standard .superpowers
sha256sum .superpowers/research/youtube-agent/architecture-audit.md \
  .superpowers/research/youtube-agent/ops-quality-audit.md \
  .superpowers/research/youtube-agent/salience-gap-audit.md \
  .superpowers/research/youtube-agent/orchestrator-synthesis.md
```

- [ ] **Step 2: Verify upstream pin provenance before changing repository metadata**

First record the current advertised branch tip:

```bash
git ls-remote --heads https://github.com/darkzOGx/youtube-automation-agent refs/heads/master
```

Use a `mktemp -d` bare repository outside the worktree to fetch the advertised `master` history, then prove the approved commit object exists and is an ancestor of the fetched tip:

```bash
probe_dir="$(mktemp -d /tmp/agenttube-provenance.XXXXXX)"
trap 'rm -rf -- "$probe_dir"' EXIT
git -C "$probe_dir" init --bare
git -C "$probe_dir" fetch --no-tags https://github.com/darkzOGx/youtube-automation-agent refs/heads/master
git -C "$probe_dir" rev-parse FETCH_HEAD
git -C "$probe_dir" cat-file -e 'e26792ae00dc1ec2434faf32632896609396dfad^{commit}'
git -C "$probe_dir" merge-base --is-ancestor e26792ae00dc1ec2434faf32632896609396dfad FETCH_HEAD
git -C "$probe_dir" show -s --format='%H%n%T%n%aI%n%an <%ae>' e26792ae00dc1ec2434faf32632896609396dfad
git -C "$probe_dir" show e26792ae00dc1ec2434faf32632896609396dfad:LICENSE | sha256sum
```

Expected: `FETCH_HEAD` matches the advertised tip; the pinned commit is reachable from it; commit metadata and static license hash are recorded for the later manifest. If reachability or the license-file check differs from the approved research, stop and request refreshed review. Do not install upstream dependencies or run its server/tests during this slice.

- [ ] **Step 3: Create and inspect the clean implementation worktree**

Require the reviewed plan/spec revision to be committed and record its exact SHA as `PLAN_BEARING_SHA`. Confirm the target path and branch name are unused, then create the isolated worktree:

```bash
test ! -e /tmp/salience-agenttube-reference-adoption-20261013
if git show-ref --verify --quiet refs/heads/work/agenttube-reference-adoption-20261013; then
  printf '%s\n' 'implementation branch already exists; stop and choose a reviewed unique name' >&2
  exit 1
fi
git worktree add -b work/agenttube-reference-adoption-20261013 \
  /tmp/salience-agenttube-reference-adoption-20261013 "$PLAN_BEARING_SHA"
```

In the fresh implementation worktree, run `git status --short --branch`, `git rev-parse HEAD`, `git remote -v`, `test ! -e .gitmodules`, and `test ! -e youtube-automation-agent`.

Expected: the worktree is clean at the reviewed plan-bearing revision, with no existing `.gitmodules` or `youtube-automation-agent/` path. Preserve the captured shared-worktree changes; do not clean, reset, stash, or stage them. Stop if any pre-existing or unexpected implementation-worktree state would be overwritten.

- [ ] **Step 4: Write the failing reference/evidence contract tests**

In `docs/v4/test_agenttube_reference.py`, test these exact cases against temporary manifests/repositories where practical:

```text
test_manifest_pins_expected_repo_branch_and_commit
test_manifest_keeps_approved_pin_when_master_tip_advances
test_pin_not_reachable_from_master_is_rejected
test_research_report_hashes_match_tracked_copies
test_report_path_must_stay_inside_repository
test_changed_report_copy_is_rejected
test_gitmodules_and_gitlink_match_the_reviewed_pin
test_initialized_submodule_must_match_gitlink
test_normal_ci_does_not_initialize_or_install_submodule
test_runtime_and_package_have_no_submodule_reference
test_evidence_keeps_all_current_release_gates
```

The gate test asserts production effects are false, budget is zero, RG0/RG1 are `HELD`, P2–P7 are `GATED`, P6 is `DISABLED`, and main is `UNMERGED`. The report test uses the four expected SHA-256 values already recorded in the approved design spec. The Gitlink test checks mode `160000` and the exact pinned SHA through `git ls-files --stage`; it does not require `git submodule status` to show an initialized checkout.

- [ ] **Step 5: Run the new checks and confirm the intended red result**

Run: `python3 docs/v4/test_agenttube_reference.py`

Expected: fail because the durable reports, evidence manifest, verifier, `.gitmodules`, and Gitlink have not been added. Confirm the failures identify those missing contracts; do not weaken assertions to make the empty repository pass.

- [ ] **Step 6: Copy the reviewed research reports verbatim**

From the clean implementation worktree, load the absolute source path captured in Step 1 and copy only these four files:

```bash
SHARED_WORKTREE="$(cat /tmp/agenttube-adoption-shared-worktree.path)"
mkdir -p docs/research/youtube-agent
cp -- "$SHARED_WORKTREE/.superpowers/research/youtube-agent/architecture-audit.md" docs/research/youtube-agent/
cp -- "$SHARED_WORKTREE/.superpowers/research/youtube-agent/ops-quality-audit.md" docs/research/youtube-agent/
cp -- "$SHARED_WORKTREE/.superpowers/research/youtube-agent/salience-gap-audit.md" docs/research/youtube-agent/
cp -- "$SHARED_WORKTREE/.superpowers/research/youtube-agent/orchestrator-synthesis.md" docs/research/youtube-agent/
```

Require each copied SHA-256 to equal the corresponding value in the approved design. Do not copy the `.superpowers/` directory.

Run: `sha256sum "$SHARED_WORKTREE"/.superpowers/research/youtube-agent/*.md docs/research/youtube-agent/*.md`

Expected: each tracked destination matches its approved SHA-256 and no extra report is copied.

- [ ] **Step 7: Add the evidence manifest with distinct evidence classes**

Create `docs/v4/agenttube-adoption-evidence.json` with schema version, upstream URL/branch hint/pin and fetched tip, pinned tree and commit metadata, static license evidence, the four repository-relative report paths and hashes, and the separately scoped upstream verification result (`npm ci`, lint, 47/47 system cases passed in the disposable research checkout; no live/provider/publishing effects ran).

The manifest must preserve both historical boundaries and actual adoption state. Record that the research inspected Salience HEAD `e7a24a38ef920da3af071a51a288b859db9aaef1` and that the historical 116 focused / 614 full non-live results belong to candidate `7878208e7a353f93bb70279ad0dd7f0760f93ec4`; never transfer those results to another SHA. Separately record the implementation worktree's exact superproject HEAD and remote feature SHA observed immediately before the adoption commit, plus whether exact-head CI/review exists for each. Re-read the active progress/release records and record their actual effect, budget, RG0/RG1, P2–P7, P6, and `main` states; preserve the approved disabled/held/gated/unmerged boundary and stop for refreshed review if current records conflict with it. Label upstream verification, historical Salience qualification, current-head repository verification, and release approval as separate evidence classes.

Run: `python3 -m json.tool docs/v4/agenttube-adoption-evidence.json`

Expected: valid JSON whose historical SHAs, actual execution HEAD/remote, current status, and evidence classes are separate fields rather than conflated prose.

- [ ] **Step 8: Add the root-level reference submodule at the exact approved commit**

Run only after Step 2 provenance passes and Step 3 confirms the target path is absent:

```bash
git submodule add --name youtube-automation-agent --branch master \
  https://github.com/darkzOGx/youtube-automation-agent youtube-automation-agent
git -C youtube-automation-agent fetch --no-tags origin e26792ae00dc1ec2434faf32632896609396dfad
git -C youtube-automation-agent checkout --detach e26792ae00dc1ec2434faf32632896609396dfad
git -C youtube-automation-agent remote get-url origin
git -C youtube-automation-agent rev-parse HEAD
git -C youtube-automation-agent status --short
```

Expected: origin is exactly the approved HTTPS URL; `HEAD` is exactly the approved SHA; the worktree is clean. `.gitmodules` must contain only the intended submodule path/URL and `branch = master`. Do not execute any upstream code or package manager.

- [ ] **Step 9: Implement the local verifier and finish the tests**

Implement the four interfaces above in `docs/v4/verify_agenttube_reference.py`. Use repository-relative, resolved paths for research files and reject any path outside the repository. Verify the manifest and report hashes, `.gitmodules`, index mode/SHA, optional initialized submodule HEAD, all `.github/workflows/*` checkout settings/commands, and application/package/deployment references. Do not treat references in the spec, research, tests, evidence manifest, or verifier itself as runtime imports.

Run: `python3 docs/v4/test_agenttube_reference.py`

Expected: all listed positive cases pass; each altered pin, path escape, modified report, initialized wrong SHA, CI submodule enablement, runtime reference, or changed gate state is rejected.

- [ ] **Step 10: Add the adoption status links without changing phase acceptance**

Add a short decision note in the blueprint linking the approved spec, tracked research, adoption evidence, and this plan. State that the submodule is reference-only and that future native work begins only after the current P1/RG0/RG1 gates. Add a dated progress checkpoint with the same links and current state. Preserve the existing P1 queue and all historical evidence text. Do not add or complete V4 requirements or describe P2–P7 as active work.

- [ ] **Step 11: Add the documentation CI guard without initializing the submodule**

Update the `verify` job in `.github/workflows/v4-documentation.yml` to run, in order:

```bash
python3 docs/v4/test_agenttube_reference.py
python3 docs/v4/verify_agenttube_reference.py
python3 docs/v4/test_checks.py
python3 docs/v4/check.py --self-test
```

Leave checkout without a `submodules` option (default false). Do not add a benchmark/install job. The verifier must fail if a later workflow opts into recursive checkout or invokes submodule setup.

- [ ] **Step 12: Run the complete bounded-slice acceptance checks**

Run:

```bash
python3 docs/v4/test_agenttube_reference.py
python3 docs/v4/verify_agenttube_reference.py
python3 docs/v4/test_checks.py
python3 docs/v4/check.py --self-test
git diff --check
git diff --cached --check
git ls-files --stage -- youtube-automation-agent
git config --file .gitmodules --get submodule.youtube-automation-agent.url
git config --file .gitmodules --get submodule.youtube-automation-agent.branch
git -C youtube-automation-agent rev-parse HEAD
git -C youtube-automation-agent status --short
```

Expected: every command succeeds; the Gitlink mode is `160000`, URL and branch hint are exact, submodule `HEAD` is the fixed pin, and the checkout is clean. Review `git diff --name-only`, the full diff, and CI workflow diff to confirm no application, dependency, migration, DB, runtime, release-ledger, or production configuration changed. Do not run the full application suite or install AgentTube dependencies for this documentation/Git-metadata slice; the existing required CI workflow supplies repository checks on the eventual commit.

- [ ] **Step 13: Stage and commit only the adoption slice**

In the implementation worktree, re-read `git status --short --branch` and confirm every changed path belongs to the adoption allowlist in File responsibilities. Compare to the clean Step 3 baseline and stop on any unexpected path. Separately reload `SHARED_WORKTREE="$(cat /tmp/agenttube-adoption-shared-worktree.path)"`, compare the modified P1-plan/design diffs and untracked `.superpowers/` file list against Step 1, and preserve them without staging. Stage only these adoption paths in the implementation worktree:

```bash
git add -- .gitmodules youtube-automation-agent \
  docs/research/youtube-agent \
  docs/v4/agenttube-adoption-evidence.json \
  docs/v4/verify_agenttube_reference.py \
  docs/v4/test_agenttube_reference.py \
  docs/v4/IMPLEMENTATION-BLUEPRINT.md docs/v4/progress.md \
  .github/workflows/v4-documentation.yml
git diff --cached --name-only
git diff --cached --check
git diff --cached
```

Expected staged paths are exactly the listed adoption files. Do not stage `.superpowers/`, the modified P1 publication-recovery plan, or any other workspace file. After reviewing the full staged diff, make one atomic commit:

```bash
git commit -m "docs(v4): pin AgentTube reference and evidence"
```

Re-run the documentation checks and verify the commit contains only the approved adoption slice. Record its SHA for Step 14:

```bash
adoption_commit="$(git rev-parse HEAD)"
printf '%s\n' "$adoption_commit" > /tmp/agenttube-adoption-commit.sha
```

Do not push or change `main` as part of this bounded slice.

- [ ] **Step 14: Fast-forward the adoption branch, verify integration, and remove the temporary worktree**

Record the adoption commit SHA. In `SHARED_WORKTREE`, confirm the branch is `work/v4-p1-publication-recovery`; compare the modified P1-plan/design diffs and untracked `.superpowers/` file list against Step 1, and preserve all of them. Confirm the adoption commit descends from the current branch head, then run a fast-forward-only merge:

```bash
SHARED_WORKTREE="$(cat /tmp/agenttube-adoption-shared-worktree.path)"
ADOPTION_COMMIT="$(cat /tmp/agenttube-adoption-commit.sha)"
cd "$SHARED_WORKTREE"
git merge-base --is-ancestor work/v4-p1-publication-recovery "$ADOPTION_COMMIT"
git merge --ff-only "$ADOPTION_COMMIT"
```

Run the bounded documentation checks again from the integrated branch: `python3 docs/v4/test_agenttube_reference.py`, `python3 docs/v4/verify_agenttube_reference.py`, `python3 docs/v4/test_checks.py`, `python3 docs/v4/check.py --self-test`, and `git diff --check`. Verify `git merge-base --is-ancestor "$ADOPTION_COMMIT" work/v4-p1-publication-recovery` succeeds, the adoption commit contains only the allowlisted files, and the shared P1-plan/design diffs and `.superpowers/` source files remain unchanged.

After the integrated checks pass, confirm the submodule worktree status is empty; if it is not, stop and preserve the changes. Deinitialize the clean submodule only in the temporary implementation worktree, then remove that worktree and delete its merged branch:

```bash
SHARED_WORKTREE="$(cat /tmp/agenttube-adoption-shared-worktree.path)"
ADOPTION_COMMIT="$(cat /tmp/agenttube-adoption-commit.sha)"
git -C /tmp/salience-agenttube-reference-adoption-20261013/youtube-automation-agent status --short
git -C /tmp/salience-agenttube-reference-adoption-20261013 submodule deinit -- youtube-automation-agent
git -C "$SHARED_WORKTREE" worktree remove /tmp/salience-agenttube-reference-adoption-20261013
git -C "$SHARED_WORKTREE" branch -d work/agenttube-reference-adoption-20261013
rm -- /tmp/agenttube-adoption-shared-worktree.path
rm -- /tmp/agenttube-adoption-commit.sha
```

Do not use force flags. Confirm `git worktree list` has no temporary implementation worktree and the adoption commit remains an ancestor of `work/v4-p1-publication-recovery`. Preserve the shared `.superpowers/` logs/research and all user changes. Do not push, merge `main`, or alter production/RG status.

## Migration and rollback

There is no database/schema migration, runtime rollout, provider migration, or production rollback because no application state or behavior changes. Rollback is a normal reviewed Git commit that removes only the submodule registration and Gitlink. First require a clean submodule worktree at the exact pin and a clean superproject worktree for the adoption paths. Then run:

```bash
git submodule deinit -- youtube-automation-agent
git rm -- youtube-automation-agent
```

Verify that Git removed the matching `.gitmodules` section; if it remains, remove only `[submodule "youtube-automation-agent"]` and stage `.gitmodules`. Retain the tracked research and adoption manifest as historical evidence, then update the blueprint/progress to mark the reference checkout removed without erasing the prior decision. Run the documentation acceptance checks and commit the rollback. Do not force-delete `.git/modules/youtube-automation-agent`; leave cached objects alone. If the submodule or superproject has local changes, stop and preserve them.

## Planning handoff

This plan covers only the repository reference/evidence slice. After review, native P2 creative/publication work, P3 analytics/learning, P4 experiments, P5 capability evolution, optional P6, and P7 rollout each need a separate plan at their existing gate. It includes only the Step 14 fast-forward integration into `work/v4-p1-publication-recovery`; it does not authorize a push, a merge to `main`, or production enablement.
