# AgentTube competitive research synthesis

**Research date:** 2026-10-12 UTC

**Upstream:** `https://github.com/darkzOGx/youtube-automation-agent`

**Reviewed branch and commit:** `master` at `e26792ae00dc1ec2434faf32632896609396dfad`

## Verification

- Full tracked checkout: 69 files, 72 commits visible on `master`.
- Recent history reviewed: 35 commits.
- `find ... -name '*.js' | xargs node --check`: PASS for every JavaScript file.
- `npm ci --no-audit --no-fund`: PASS; 495 packages installed in the disposable checkout.
- `npm run lint`: PASS, exit 0.
- `npm test`: PASS, 47/47 custom system cases, exit 0. No setup, OAuth, live provider, paid generation, or real publishing call ran.
- External tracked checkout remained clean after installation and tests.

## Evidence reports

- Architecture audit SHA-256: `23daa3962eda5f876be12f166966dc84fc06e9395d904992ff7672ed75564485`
- Operations/quality audit SHA-256: `6364f88a7acdb24e6e5f1449248a4b0c0e89c142441c8d460d255baa0eee28f8`
- Salience gap audit SHA-256: `93c0822ca6ed4b965b699d8b28567ad51d537464783e3a482c9c621990fc331a`

## Finding

AgentTube is a strong local YouTube product benchmark. Its completed operator flows include guided onboarding, production readiness, durable generation checkpoints, scene repair, Shorts, provenance review, scheduling/publishing, comments, analytics, controlled packaging experiments, and an outcome/ROI view. Its agents are same-process JavaScript classes and its autonomous operator executes plan items sequentially. It is not a distributed multi-worker platform and has no general multi-platform publisher/analytics abstraction.

Salience already has the stronger platform substrate: Temporal workflows, PostgreSQL canonical records, outbox/inbox, stable operations, budget liabilities, scoped authority and revocation, typed recovery holds, immutable artifacts, structured rights, platform-neutral publication contracts, and bounded parallel teams. Those are locally qualified fixture capabilities or planned V4 contracts; production remains disabled and the operator-facing product loop is incomplete.

Do not import AgentTube as the Salience runtime. Add it as a pinned, reference-only root submodule and implement selected product behaviors natively against Salience contracts. Do not copy its optional authentication, all-interface server bind, plaintext secret files, SQLite/cron coordination, silent provider substitution, unversioned inline schema creation, or simplified experiment inference.

## Recommended submodule contract

- Root path: `youtube-automation-agent/`.
- `.gitmodules` records the upstream URL and `branch = master` only as an explicit update hint.
- Gitlink pins `e26792ae00dc1ec2434faf32632896609396dfad`; builds never float with upstream `master`.
- The submodule is research/reference material and is not imported, packaged, started, or granted credentials by Salience.
- Normal Salience CI does not install or execute submodule dependencies. Any future benchmark job is isolated, non-live, manually/schedule triggered, and source-pinned.
- Upstream updates require a reviewed commit diff, license/security/dependency check, refreshed capability matrix, and a new pinned gitlink commit.

## Recommended native adoption sequence

1. Complete the active P1 rollback-floor qualification and evidence repair without reopening finished P0/P1 work.
2. Add a versioned capability-manifest and readiness-report contract for platform, creative, model, analytics, and engagement adapters. Readiness never enables effects.
3. Add a platform-neutral content/scene graph with immutable media manifests, selective repair, downstream invalidation, resumable generation, rights, provenance, cost, and disclosure state.
4. Finish YouTube upload/readback/reconciliation, then qualify Instagram, LinkedIn, TikTok, and other adapters separately according to official capabilities and access.
5. Add per-platform read-only analytics and engagement adapters with canonical metric windows, maturity, missingness, corrections, attribution, and source lineage.
6. Add approved learning proposals and frozen experiments with assignment/exposure evidence, guardrails, control restoration, separate promotion, and rollback.
7. Expand Temporal-backed parallel teams from read-only fixtures to separately qualified effect classes under scoped permits, bounded fan-out, cost limits, and typed recovery.
8. Add a guided operator console that auto-discovers configured capabilities, proposes a versioned configuration, runs explicit bounded probes, and shows remediation. It references secrets through the resolver and cannot silently select or activate a provider.

Production effects, RG0/RG1, P2-P7, optional P6, and `main` remain unchanged by this research.
