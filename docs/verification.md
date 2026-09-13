# Verification

## Earlier Phase 1–6 Baseline

On 2026-09-12, the merged `main` checkout passed the full Python suite: 99
tests passed, one live test was intentionally skipped, and the only warning was
a third-party Starlette deprecation warning. The project-owned browser verifier
also passed all seven governed Chromium scenarios using Playwright 1.62.0 and
Chromium 151.0.7922.34. The retained local receipt location is recorded in
`docs/implementation-progress.md`; generated evidence is intentionally ignored
by Git because it can contain captured public-page artifacts.

Run `scripts/verify-phases-1-4.sh` from a prepared local checkout. It creates a
unique Compose project, applies all migrations, executes Phases 1–4 e2e tests,
and removes only its project volumes on exit.

The Phase 1 Compose recovery test is separate because it builds an independent
provider, hard-exits a worker after remote acceptance, restarts that worker, and
requires a single accepted effect plus reconciliation.

For Phases 5–6, reuse active services when disk is constrained:

```bash
pytest tests/test_config.py tests/unit/test_research_connectors.py \
  tests/e2e/test_phase5_durable_research.py \
  tests/e2e/test_phases_5_6_intelligence_loop.py \
  tests/evals/test_phase5_signal_eval.py tests/evals/test_phase6_packaging_eval.py \
  tests/evals/test_intelligence_safety_eval.py \
  tests/integration/test_intelligence_control_api.py -q
```

This focused command verifies configuration, source connector boundaries,
restart-safe source-to-brief lineage, deterministic ranking and packaging,
prompt-injection/contradiction safety, and the public intelligence control API.
Use the PostgreSQL and Temporal service addresses for `TEST_DATABASE_URL` and
`TEST_TEMPORAL_TARGET` when running outside their Compose network.

Set `LIVE_HACKER_NEWS_ITEM_URL` for `pytest -m live tests/live/test_research_smoke.py -q`.
Inspect `df -h .` and `docker system df` before browser installation or a broad
Compose verifier; no global Docker cleanup is part of verification.

## Browser Evidence

Run the single operator/CI workflow below on supported Linux hosts:

```bash
bash scripts/verify-browser-evidence.sh --install
```

Without `--install`, the verifier reports `NOT RUN` with an actionable setup
message if the project venv or Chromium runtime is absent. With it, the script
uses the official Playwright Chromium dependency/browser installation path,
launches Chromium once as a preflight, then runs the browser-marked suite.
The suite proves JavaScript rendering, structured text/PNG/ZIP evidence,
source/fetch/version/run/hash receipts, blocked domains and redirects, disabled
downloads, retained timeout traces without secrets, and untrusted hostile text.
It retains every run below `artifacts/browser-evidence/`; a test failure prints
that location for inspection. It never performs global Docker or system cleanup.

## Phase 7–8 Creative Production

The release gate has a dedicated crash-and-recovery fixture: it starts a
non-dry run with an explicit budget, interrupts after provider acceptance,
restarts a worker, reconciles the same accepted request, settles one actual-cost
entry, converges a duplicate verified webhook, verifies asset provenance and
source-to-ready-package lineage, and proves the approved candidate is not
directly mutable. The focused verifier below is the complete required fixture
check for the creative-production boundary.

Run the project-owned verifier from a prepared checkout:

```bash
bash scripts/verify-phases-7-8.sh
```

The script starts or reuses only the project PostgreSQL and Temporal fixtures,
applies Alembic migrations through `0008_creative_release_gate`, and drives the
creative control API, CLI, SDK, fixture provider, full Temporal workflow, and
interruption/restart recovery. The asserted completed path is a selected brief
through claim-linked script and direction, provider capability selection,
policy/budget/rights authorization, durable reservation/actual settlement,
idempotent provider submission/reconciliation, verified webhook deduplication,
hash-stable asset import, distribution governance, disclosure/approval,
immutable decision versions, and ready-package lineage.

The script distinguishes a passing fixture path from unavailable or unexecuted
optional dependencies. It reports `NOT RUN` rather than a false pass for FFmpeg
media execution (the fixture uses a controlled media probe even when binaries
exist), C2PA signer-backed media execution, and live provider/publishing work;
Phase 7–8 deliberately performs no live publish effect.

## Architecture Evidence

The Phase 1–8 Archify source and standalone HTML remain the release-gate
evidence. `deliver` validated all nine showcase checks with zero warnings/errors;
the specification SHA-256 is
`f06d8d549297cd8614c2da36e266a133160673220ccfe441acde565540d6fd97` and the
HTML SHA-256 is `e888dd64671a5c456f166234ec84ae422b223e4944772c69ff6c704512260603`.
Automated Chromium evidence passed containment/readability at 1440x900,
1600x1000, 1920x1080, and 2048x1320, with no overflow. The linked HTML is a
visual explanation of the fixture-verified boundary, not evidence of a live
provider or publishing integration.

For a full non-live regression before integration, run:

```bash
pytest tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q
```

## Phase 9 Governed Publishing

Run the complete fixture-first publishing gate from a prepared checkout:

```bash
bash scripts/verify-phase-9.sh
```

The verifier starts/reuses project PostgreSQL and Temporal, applies migrations
through `0012_publication_profile_scope`, and checks publisher contract/registry,
canonical persistence, scoped API/CLI/SDK surfaces, immutable scheduling,
policy/rights/budget/approval gates including final-write reauthorization, fixture submission/reconciliation, duplicate-safe
signed webhook receipts, and an interruption after remote acceptance. Its latest
recorded run passed 58 focused fixture tests and the complete 232-test non-live
suite, each with only the existing third-party Starlette deprecation warning. It
also executes the YouTube contract and live-status test. The verifier disables
pytest's optional cache provider so a constrained host cannot turn cache writes
into a false release failure.
A separate invocation of the complete non-live command also passed all 232
tests after the final documentation refresh.

The live status is intentionally `NOT RUN: YOUTUBE_PUBLISHER_CONNECTION_REF is
not configured` unless an operator explicitly supplies its connection reference
and enables the private-only smoke configuration. Even then the current smoke
returns `NOT RUN` until an operator-managed private test asset and credential
lease are available; it never attempts a public video post.

## Phase 1–9 Architecture Evidence

`docs/salience-phase-1-9.architecture.html` was delivered from its checked JSON
source with all nine Archify showcase checks, zero warnings, and 18
repository-grounded references. Its SHA-256 values are
`ed4313a4a0c21891442846ab10eb91de44b6295523dc86abc6d651ef6bfa02dc` for the
specification and
`c914a1d51a012f8aa3e87b1165014515693ae063431b5c09bf53939a791df95e` for the
HTML at repository revision `888552cf09f979c1f85f730a3d5a02969d5684b0`.
The current host has no Chrome/Chromium executable, so the current
automated containment/readability capture is `NOT RUN`; the deterministic
Archify composition checks remain green. The visual explains the
fixture-verified boundary and is not evidence of an enabled live publisher.
