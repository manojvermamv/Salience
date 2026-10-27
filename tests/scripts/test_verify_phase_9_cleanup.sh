#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

cat >"$tmp/bin/docker" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$VERIFY_PHASE9_DOCKER_LOG"

if [[ ${1:-} == compose && ${2:-} == ps && ${3:-} == -q ]]; then
  if [[ ${4:-} == postgres ]]; then
    printf 'postgres-container\n'
  else
    printf 'temporal-container\n'
  fi
  exit 0
fi

if [[ ${1:-} == inspect ]]; then
  printf '172.20.0.2\n'
  exit 0
fi

if [[ ${1:-} == compose && ${2:-} == exec ]]; then
  if [[ $* == *'CREATE DATABASE '* && ${VERIFY_PHASE9_MODE:-} == create-fails ]]; then
    exit 1
  fi
  if [[ $* == *'DROP DATABASE '* && ${VERIFY_PHASE9_MODE:-} == cleanup-fails ]]; then
    exit 1
  fi
fi

exit 0
STUB

cat >"$tmp/bin/python-stub" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$VERIFY_PHASE9_PYTHON_LOG"
if [[ $* == *'import secrets'* ]]; then
  printf '%s\n' "${VERIFY_PHASE9_UUID:-0123456789abcdef0123456789abcdef}"
elif [[ $* == *'run_live_smoke'* ]]; then
  printf 'NOT RUN: shell regression stub\n'
fi
STUB

cat >"$tmp/bin/node" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB

chmod +x "$tmp/bin/docker" "$tmp/bin/python-stub" "$tmp/bin/node"

run_verifier() {
  local mode=$1
  local output=$2
  : >"$tmp/docker.log"
  : >"$tmp/python.log"
  if PATH="$tmp/bin:$PATH" \
    PYTHON_BIN="$tmp/bin/python-stub" \
    VERIFY_PHASE9_MODE="$mode" \
    VERIFY_PHASE9_DOCKER_LOG="$tmp/docker.log" \
    VERIFY_PHASE9_PYTHON_LOG="$tmp/python.log" \
    VERIFY_PHASE9_UUID=0123456789abcdef0123456789abcdef \
    bash "$root/scripts/verify-phase-9.sh" >"$output" 2>&1; then
    return 0
  else
    return $?
  fi
}

if run_verifier create-fails "$tmp/create-fails.log"; then
  printf 'Expected database creation failure to fail verification\n' >&2
  exit 1
fi
if grep -Fq 'DROP DATABASE' "$tmp/docker.log"; then
  printf 'Verifier attempted to drop a database after creation failed\n' >&2
  exit 1
fi

if run_verifier cleanup-fails "$tmp/cleanup-fails.log"; then
  printf 'Expected database cleanup failure to fail verification\n' >&2
  exit 1
fi
grep -Fq 'FAIL: Disposable Phase 9 test database cleanup failed' \
  "$tmp/cleanup-fails.log" || {
    printf 'Verifier did not report the cleanup failure\n' >&2
    exit 1
  }
grep -Fq 'DROP DATABASE' "$tmp/docker.log" || {
  printf 'Verifier did not attempt cleanup after successful creation\n' >&2
  exit 1
}

printf 'Phase 9 verifier cleanup contract passed\n'
