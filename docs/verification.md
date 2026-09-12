# Verification

Run `scripts/verify-phases-1-4.sh` from a prepared local checkout. It creates a
unique Compose project, applies all migrations, executes Phases 1–4 e2e tests,
and removes only its project volumes on exit.

The Phase 1 Compose recovery test is separate because it builds an independent
provider, hard-exits a worker after remote acceptance, restarts that worker, and
requires a single accepted effect plus reconciliation.

For Phases 5–6, reuse active services when disk is constrained:

```bash
pytest tests/e2e/test_phase5_durable_research.py \
  tests/e2e/test_phases_5_6_intelligence_loop.py \
  tests/evals/test_phase5_signal_eval.py tests/evals/test_phase6_packaging_eval.py \
  tests/evals/test_intelligence_safety_eval.py -q
```

Set `LIVE_HACKER_NEWS_ITEM_URL` for `pytest -m live tests/live/test_research_smoke.py -q`.
Inspect `df -h .` and `docker system df` before browser installation or a broad
Compose verifier; no global Docker cleanup is part of verification.
