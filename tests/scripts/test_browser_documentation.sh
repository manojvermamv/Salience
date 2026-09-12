#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root_dir"

for document in README.md docs/deployment.md docs/verification.md; do
  if ! grep -F 'bash scripts/verify-browser-evidence.sh --install' "$document" >/dev/null; then
    printf 'missing browser verifier command in %s\n' "$document" >&2
    exit 1
  fi
done
for document in docs/research.md docs/verification.md; do
  if ! grep -F 'artifacts/browser-evidence/' "$document" >/dev/null; then
    printf 'missing browser evidence path in %s\n' "$document" >&2
    exit 1
  fi
done
for document in docs/research.md docs/limitations.md; do
  if ! grep -F 'untrusted_external' "$document" >/dev/null; then
    printf 'missing untrusted evidence policy in %s\n' "$document" >&2
    exit 1
  fi
done
if grep -F 'no browser binary/live-browser smoke was installed' docs/limitations.md >/dev/null; then
  printf 'obsolete browser limitation remains in docs/limitations.md\n' >&2
  exit 1
fi
