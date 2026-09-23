# V4 documentation transition checkpoint

Historical scope below was documentation-only. The subsequent authorized correction/P0 execution is recorded at the end; no production effects or Git history rewriting are permitted.

Baseline: `8e50d68efbf2bec2aabfb2fe3226e01635de2adb`, inspected 2026-09-22.

- [x] Inventory before consolidation: `inventory.json` contains 360 files, including all current docs, master README, prompts, ArchV4 sources, repository-owned skill instructions, and discovered worktree/checkpoint/visual evidence. Dependency installations and generated caches are explicitly excluded.
- [x] Read both complete ArchV4 sources and inspect present schema, workflow registration, control/auth boundaries, adapters, tests, and Compose configuration.
- [x] Draft a standalone blueprint with 70 requirement-to-phase/dependency/test/migration/release mappings and 17 current-state evidence groups.
- [x] Validate before consolidation: `python3 docs/v4/check.py --draft --self-test` passed all mappings, source incorporation, 249 evidence locks, 360 original inventory hashes and six negative controls.
- [x] Archive 62 superseded prose documents with exact-byte recovery, retain instruction files and historical visual artifacts, repair navigation and preserve heading bookmarks.
- [x] Re-run documentation checks and six negative controls; validate 82 Markdown files, five local HTML visuals, all 360 dispositions and 249 unchanged application evidence files. A simulated clean checkout also passes without ignored local/worktree artifacts.
- [x] Existing Phase 7–8, Phase 9 and browser documentation/command-contract checks pass. Application diff is empty; Git HEAD remains the original baseline. No application feature or Git history change occurred.

Documentation objective complete. Future implementation begins at blueprint P0; all V4 application phases remain planned. Fresh application evidence is 226 passes and six browser failures due to missing externally provisioned Chromium, recorded in `validation.md` and blueprint C20. Documentation CI is configured, but remote workflow execution and branch protection are not claimed.

Findings carried into the blueprint: current auth accepts a caller scope header after one shared bearer token; durable job creation and Temporal starts are separate calls without a transactional outbox; no V4 cycle/context/observation/experiment/training entities were found in the schema/module inventory; current YouTube adapter starts an upload session but does not upload or verify publication; Garage configuration referenced by Compose is absent. These remain explicit future release obligations.

## Correction and P0 execution — 2026-09-22

Continue the existing dirty main workspace; preserve its documentation consolidation and archive. Do not merge stale worktrees or rewrite history. User authorizes P0 application changes, not P1–P7 implementation.

- [x] Reinspect configuration, authentication dependency, canonical schema, storage adapters, telemetry, worker and test setup; reuse existing PostgreSQL/Temporal, secret/scoping contracts and OTel SDK.
- [x] Red: `python3 docs/v4/test_checks.py` failed twice (missing gate validator; P0 prematurely requiring outbox).
- [x] Correct prose gates and split five foundational obligations without deleting original requirements. Resolve re-admission, G0/G1 accounting, optional capabilities/R58 and promotion/invalidation sequencing in the blueprint.
- [x] Green: gate tests 2/2; `check.py --self-test` passes 75 mapped obligations and unchanged original sources/evidence.
- [x] Validate Markdown structure/literals and render six Mermaid diagrams before application changes.
- [x] Red/green P0 identity and isolated effects-locked configuration using maintained JWT verification and database authority/audit.
- [x] Red/green immutable storage/readback/integrity and retention-safe lifecycle hooks; qualify disposable real S3 separately from mocks.
- [x] Red/green trace propagation/redaction and dependency readiness; full outbox test remains P1.
- [x] Run regression, migrations/rollback, review actual evidence, refresh traceability lock and explicitly hold RG0 for outstanding external qualification.

Dependency decision: adopt already-installed PyJWT 2.14.0 (MIT) with cryptography 50.0.1 for fixed-RS256 issuer/audience/required-time verification, not a custom token issuer or OAuth server. Server-side grants remain canonical. Official API review: https://pyjwt.readthedocs.io/en/stable/api.html . Retain boto3/S3 and OTel SDK; conditional writes require actual service verification, not an assumed Garage capability. References: https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html and https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/ . Mermaid 12.0.0 (MIT, npm integrity verified) is an isolated documentation verifier, not an application dependency.

### 2026-09-23 verification checkpoint

All six authoritative diagrams parse/render using Mermaid 12.0.0 and existing `/usr/bin/chromium`; structural checks of 82 active Markdown documents find no export corruption. Original sources remain byte-identical. Artifacts: `artifacts/v4-p0/diagrams/` (local, regenerable with `verify_diagrams.py`). Gate and Markdown negative tests pass 6/6.

P0 changes in progress: isolated JWT/database-authority API; append-only access decisions; effects and worker lockout; bounded local control; immutable object adapters, persisted verification/quarantine/reference/retention hooks; exact active OTel span identities and redaction; additive migrations 0013/0014. Migration was initially rejected for multiple prepared SQL commands, repaired with per-statement execution. A full nonbrowser regression found a memory-store metadata compatibility break (249 pass, 1 fail); repaired by keeping the integrity digest private, preserving caller metadata. Focused repair/migration/auth/trace tests then passed 31/31. Final full regression is running, not yet claimed green.

Real pinned Garage v2.3.0 **ignored `If-None-Match: *`**, overwriting an existing key. Added a startup/first-write capability probe that rejects such an endpoint before storing caller bytes. Two positive qualification tests were correctly NOT RUN; the negative real-service rejection test passed. Do not call this a qualified storage service. Investigating mature replacement rather than stopping at a locally resolvable dependency: SeaweedFS 4.47 (Apache-2.0, released 2026-09-14, tag `c5073360007d28385a33426a42ac3e4ec504c5a3`) documents atomic conditional writes; testing an isolated digest-pinned container next. Sources: [conditional operations](https://github.com/seaweedfs/seaweedfs/wiki/S3-Conditional-Operations), [release](https://github.com/seaweedfs/seaweedfs/releases/tag/4.47), [license](https://github.com/seaweedfs/seaweedfs/blob/4.47/LICENSE). MinIO was not selected because its upstream repository is archived; no custom storage engine is being built.

Provisioned the existing pinned Playwright headless runtime (Chromium 151.0.7922.34, revision 1234) using `.venv/bin/python -m playwright install chromium --only-shell`; this is local test tooling, not production browser enablement. Independent P0 code review is running per requesting-code-review skill. No commit, push, history rewrite, live provider operation, or P1 implementation occurred.

### Dependency recovery and inspection

SeaweedFS 4.47 image digest `sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882` passed the real storage suite, including concurrent create, exact retry, corruption/missing bytes, scoped credentials and lifecycle quarantine. Positive tests now use this qualified disposable backend and no longer skip; Garage remains an explicit negative regression. No production backend was replaced or exposed. The original full non-live regression, collected before this replacement, completed with 259 passed/2 skipped; it is superseded by a new full run on final code.

Independent review could not run: the reviewer tool returned an account usage-limit error, confirmed by the agent status, not a review approval. No repeated retry can supply independent evidence without the external quota changing. Root performed local inspection instead: fixed an invented intermediate API parent span; prevented automatic exception-message export; enforced inactive-workspace denial; bounded S3 reads/writes; and fixed the delete-before-database-commit crash race by rechecking stored bytes under the reference lock and persisting quarantine before rejecting a missing object. Added targeted regressions; final full verification remains in flight. Independent review is explicitly NOT RUN and remains required before production rollout.

Read-only local inventory at migration `0014_object_inventory`: 87 tables, 565 nonterminal local job/history references, 940 fixture external-effect references, 100 reserved fixture liabilities at capture time; these are accumulated local test records, not production obligations and are not automatically resumed/settled. Safe inventory is `artifacts/v4-p0/runtime-inventory.json`, reproducible with `capture_runtime.py`. Production history/reconciliation inventory still requires its actual authorized environment.

### Final executable checkpoint — 2026-09-23

- Corrected blueprint, future phase decisions, original source coverage and all six diagrams validated; 75 mapped obligations remain acyclic. Documentation regression tests: 7 passed; full documentation validator and six original negative controls pass, including a simulated clean checkout.
- R22 edge-secret baseline now pins server-owned destination/audience/workspace/provider/version/exact scopes; leases expire at reveal, revoke and cannot serialize or bypass through the legacy resolver. Red import failure followed by passing focused tests is preserved.
- Final P0-focused suite: **37 passed**. Final complete non-live suite: **269 passed, zero failed, zero skipped**, 254.27 seconds; browser and durable recovery tests included. A prior 266-pass/1-failure run exposed real storage startup readiness; repaired with verified data-plane warmup rather than accepting bucket creation alone.
- Refreshed 262 code/config/test evidence locks after local inspection; original 249-file baseline lock remains in `evidence-baseline.json`. All original source/archive/instruction hashes still verify. Compose validation, dependency consistency, original documentation shell tests and whitespace checks pass.
- Local implementation/verification is complete. **RG0 production release remains HELD**, with NOT RUN external issuer/TLS/key-rotation/restricted database-role, encrypted managed-storage/restore, telemetry/alerts, remote repository enforcement and independent review requirements in `p0-release.json`. The reviewer service is blocked by external quota. Required production profile environment inputs are absent; fixture values are not approvals.
- No production effects, production ingress, P1 code, commit, push or history rewrite. Resume at the explicit RG0 external qualification items, not at reimplementing the completed P0 work. Do not enter P1 or represent RG0 as passed until those applicable checks are resolved and evidenced.

### Requested branch publication — 2026-09-23

User subsequently requested a commit and push on a new branch. Publication target: `origin/feat/archv4-p0-foundation`, based on `8e50d68efbf2bec2aabfb2fe3226e01635de2adb`. Include the preserved documentation consolidation and P0 work together; leave `main` and the other worktrees unchanged. This does not release RG0 or authorize production effects. Pre-commit verification is being rerun on this tree; fresh results are retained separately under `artifacts/v4-p0/branch-verification.*` so the original qualification evidence hashes remain valid.

The interrupted pre-commit run produced no final result and is not counted as passing. Its replacement completed successfully: **269 passed, zero failed, zero skipped**, 206.71 seconds, with one existing Starlette/AnyIO deprecation warning. Fresh documentation regression tests: **7 passed**; the complete documentation validator and six negative controls pass (75 obligations, 262 evidence files, 360 inventory entries, 82 Markdown files and five HTML files). Dependency consistency and staged whitespace checks pass. RG0 remains **HELD**; this publication is a source checkpoint, not production authorization.
