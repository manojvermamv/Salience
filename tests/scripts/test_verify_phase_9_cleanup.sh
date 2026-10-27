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

cat >"$tmp/bin/git" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$VERIFY_PHASE9_GIT_LOG"
if [[ $* == *'status --porcelain'* ]]; then
  case ${VERIFY_PHASE9_GIT_MODE:-clean} in
    dirty|dirty-untracked) printf '?? untracked-fixture-file\n' ;;
    dirty-tracked) printf ' M tracked-fixture-file\n' ;;
  esac
elif [[ $* == *'rev-parse --verify HEAD^{commit}'* ]]; then
  printf '%s\n' "$VERIFY_PHASE9_HEAD"
fi
STUB

chmod +x "$tmp/bin/docker" "$tmp/bin/python-stub" "$tmp/bin/node" "$tmp/bin/git"

run_verifier() {
  local mode=$1
  local output=$2
  local git_mode=${3:-clean}
  local head=${4:-0123456789abcdef0123456789abcdef01234567}
  : >"$tmp/docker.log"
  : >"$tmp/python.log"
  : >"$tmp/git.log"
  if PATH="$tmp/bin:$PATH" \
    PYTHON_BIN="$tmp/bin/python-stub" \
    VERIFY_PHASE9_MODE="$mode" \
    VERIFY_PHASE9_GIT_MODE="$git_mode" \
    VERIFY_PHASE9_HEAD="$head" \
    VERIFY_PHASE9_DOCKER_LOG="$tmp/docker.log" \
    VERIFY_PHASE9_PYTHON_LOG="$tmp/python.log" \
    VERIFY_PHASE9_GIT_LOG="$tmp/git.log" \
    VERIFY_PHASE9_UUID=0123456789abcdef0123456789abcdef \
    bash "$root/scripts/verify-phase-9.sh" >"$output" 2>&1; then
    return 0
  else
    return $?
  fi
}

if ! run_verifier success "$tmp/clean.log"; then
  printf 'Expected clean source verification to succeed\n' >&2
  cat "$tmp/clean.log" >&2
  exit 1
fi
grep -Fxq 'SOURCE_COMMIT: 0123456789abcdef0123456789abcdef01234567' \
  "$tmp/clean.log" || {
    printf 'Verifier did not print the exact source commit\n' >&2
    exit 1
  }
source_line=$(grep -n -F 'SOURCE_COMMIT:' "$tmp/clean.log" | cut -d: -f1)
migration_line=$(grep -n -F 'Canonical migrations apply through' "$tmp/clean.log" | cut -d: -f1)
if (( source_line >= migration_line )); then
  printf 'Source commit provenance was printed after migrations\n' >&2
  exit 1
fi

if run_verifier success "$tmp/dirty.log" dirty; then
  printf 'Expected dirty source verification to fail\n' >&2
  exit 1
fi
grep -Fq 'FAIL: Source repository is dirty' "$tmp/dirty.log" || {
  printf 'Verifier did not identify dirty source state\n' >&2
  exit 1
}
if grep -Eq 'CREATE DATABASE|DROP DATABASE' "$tmp/docker.log"; then
  printf 'Verifier touched the disposable database after dirty-source failure\n' >&2
  exit 1
fi

if run_verifier success "$tmp/dirty-tracked.log" dirty-tracked; then
  printf 'Expected tracked changes to fail source verification\n' >&2
  exit 1
fi
grep -Fq 'FAIL: Source repository is dirty' "$tmp/dirty-tracked.log" || {
  printf 'Verifier did not identify tracked source changes\n' >&2
  exit 1
}
if grep -Eq 'CREATE DATABASE|DROP DATABASE' "$tmp/docker.log"; then
  printf 'Verifier touched the disposable database after tracked-source failure\n' >&2
  exit 1
fi

if run_verifier success "$tmp/invalid-sha.log" clean malformed-sha; then
  printf 'Expected malformed source commit verification to fail\n' >&2
  exit 1
fi
grep -Fq 'FAIL: Git HEAD is not a full commit SHA' "$tmp/invalid-sha.log" || {
  printf 'Verifier did not reject malformed source commit output\n' >&2
  exit 1
}
if grep -Eq 'CREATE DATABASE|DROP DATABASE' "$tmp/docker.log"; then
  printf 'Verifier touched the disposable database after malformed SHA failure\n' >&2
  exit 1
fi

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
