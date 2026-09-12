#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
temporary_dir=$(mktemp -d)
trap 'rm -rf "$temporary_dir"' EXIT

set +e
output=$(SALIENCE_BROWSER_EVIDENCE_VENV="$temporary_dir/missing" \
  bash "$root_dir/scripts/verify-browser-evidence.sh" 2>&1)
status=$?
set -e

if [[ $status -ne 2 ]]; then
  printf 'expected unavailable verifier status 2, received %s\n%s\n' "$status" "$output" >&2
  exit 1
fi
if [[ $output != *"NOT RUN"* || $output != *"setup-browser-evidence.sh"* ]]; then
  printf 'expected actionable NOT RUN output\n%s\n' "$output" >&2
  exit 1
fi
