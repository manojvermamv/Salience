#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
result=0

pass() {
  printf 'PASS: %s\n' "$1"
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  result=1
}

not_run() {
  printf 'NOT RUN: %s\n' "$1"
}

if [[ -n ${PYTHON_BIN:-} ]]; then
  python_bin=$PYTHON_BIN
else
  common_git_dir=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)
  shared_root=$(dirname "$common_git_dir")
  python_bin="$root/.venv/bin/python"
  if [[ ! -x $python_bin && -x $shared_root/.venv/bin/python ]]; then
    python_bin="$shared_root/.venv/bin/python"
  fi
fi

if [[ ! -x $python_bin ]]; then
  fail "Python virtual environment is unavailable; set PYTHON_BIN or create $root/.venv"
  exit "$result"
fi

if ! command -v docker >/dev/null 2>&1; then
  fail "Docker is required for the PostgreSQL and Temporal fixture services"
  exit "$result"
fi

cd "$root"

if bash tests/scripts/test_phase_9_documentation.sh; then
  pass "Phase 9 documentation contract"
else
  fail "Phase 9 documentation contract"
  exit "$result"
fi

if docker compose up -d postgres temporal; then
  pass "PostgreSQL and Temporal fixture services are available"
else
  fail "PostgreSQL and Temporal fixture services could not be started"
  exit "$result"
fi

postgres_id=$(docker compose ps -q postgres)
if [[ -z $postgres_id ]]; then
  fail "PostgreSQL Compose container was not found"
  exit "$result"
fi

postgres_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$postgres_id")
if [[ -z $postgres_ip ]]; then
  fail "PostgreSQL container has no reachable network address"
  exit "$result"
fi

database_url="postgresql+asyncpg://salience:salience@${postgres_ip}:5432/salience"
if DATABASE_URL="$database_url" "$python_bin" -m alembic upgrade head; then
  pass "Canonical migrations apply through 0012_publication_profile_scope"
else
  fail "Canonical migrations did not apply"
  exit "$result"
fi

if "$python_bin" -m pytest -p no:cacheprovider \
  tests/contracts/test_publisher_registry.py \
  tests/unit/test_publication_contracts.py \
  tests/unit/test_publication_governance.py \
  tests/unit/test_publication_delivery.py \
  tests/unit/test_publication_schedule.py \
  tests/integration/test_publication_migrations.py \
  tests/integration/test_publication_repository.py \
  tests/integration/test_fixture_publisher.py \
  tests/integration/test_publication_control_api.py \
  tests/integration/test_youtube_publisher_contract.py \
  tests/e2e/test_phase9_publication_recovery.py \
  tests/e2e/test_phase9_governed_publishing.py \
  tests/e2e/test_phase7_creative_recovery.py \
  tests/unit/test_cli.py \
  tests/unit/test_sdk.py -q; then
  pass "Phase 9 fixture release gate: governed persistence, policy, cost, scheduling, reconciliation, callbacks, control clients, and private YouTube session boundary"
else
  fail "Phase 9 fixture release-gate test suite"
fi

if "$python_bin" -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q; then
  pass "Full non-live regression suite"
else
  fail "Full non-live regression suite"
fi

if "$python_bin" -m compileall -q src; then
  pass "Python compilation"
else
  fail "Python compilation"
fi

if git diff --check; then
  pass "Whitespace and patch integrity"
else
  fail "Whitespace and patch integrity"
fi

if node .agents/skills/archify/bin/archify.mjs validate architecture \
  docs/salience-phase-1-9.architecture.json --quality showcase --repo-root . --json; then
  pass "Phase 1–9 Archify validation"
else
  fail "Phase 1–9 Archify validation"
fi

live_status=$(PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -c 'from salience.publication.youtube import run_live_smoke; print(run_live_smoke())')
case "$live_status" in
  PASS)
    pass "Private YouTube live smoke"
    ;;
  FAIL:*)
    fail "$live_status"
    ;;
  NOT\ RUN:*)
    not_run "${live_status#NOT RUN: }"
    ;;
  *)
    fail "YouTube live smoke returned an unrecognized status: $live_status"
    ;;
esac

if "$python_bin" -m pytest -p no:cacheprovider -m live tests/live/test_youtube_publisher_smoke.py -q; then
  pass "YouTube live-smoke status contract"
else
  fail "YouTube live-smoke status contract"
fi

exit "$result"
