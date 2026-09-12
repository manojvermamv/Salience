# Verification

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
