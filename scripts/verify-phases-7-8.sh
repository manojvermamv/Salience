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

if DATABASE_URL="postgresql+asyncpg://salience:salience@${postgres_ip}:5432/salience" "$python_bin" -m alembic upgrade head; then
  pass "Canonical migrations apply through 0007_creative_lineage"
else
  fail "Canonical migrations did not apply"
  exit "$result"
fi

if "$python_bin" -m pytest \
  tests/integration/test_creative_control_api.py \
  tests/unit/test_cli.py \
  tests/unit/test_sdk.py \
  tests/e2e/test_phases_7_8_creative_loop.py \
  tests/e2e/test_phase7_creative_recovery.py -q; then
  pass "Phase 7–8 fixture control loop, recovery, CLI, SDK, and API contracts"
else
  fail "Phase 7–8 fixture control loop test suite"
fi

if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  pass "FFmpeg and ffprobe are available for optional media-engine execution"
else
  not_run "FFmpeg media-engine execution — ffmpeg and/or ffprobe are not installed"
fi

if command -v c2patool >/dev/null 2>&1 && [[ -n ${C2PA_SIGNER_REF:-} ]]; then
  pass "C2PA signing tool and signer reference are configured"
else
  not_run "C2PA signing — c2patool and C2PA_SIGNER_REF are not both configured"
fi

not_run "Live creative-provider and social-publishing verification — Phase 7–8 uses deterministic fixture providers and implements no publishing connector"

exit "$result"
