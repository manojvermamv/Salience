# Verification

Run `scripts/verify-phases-1-4.sh` from a prepared local checkout. It creates a
unique Compose project, applies all migrations, executes Phases 1–4 e2e tests,
and removes only its project volumes on exit.

The Phase 1 Compose recovery test is separate because it builds an independent
provider, hard-exits a worker after remote acceptance, restarts that worker, and
requires a single accepted effect plus reconciliation.
