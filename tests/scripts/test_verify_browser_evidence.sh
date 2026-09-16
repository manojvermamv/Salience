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
if [[ $output != *"NOT RUN"* || $output != *"outside Salience"* ]]; then
  printf 'expected actionable NOT RUN output\n%s\n' "$output" >&2
  exit 1
fi

fake_bin="$temporary_dir/bin"
mkdir "$fake_bin"
printf '#!/usr/bin/env sh\nexit 97\n' > "$fake_bin/bash"
chmod +x "$fake_bin/bash"

set +e
install_output=$(PATH="$fake_bin:$PATH" \
  SALIENCE_BROWSER_EVIDENCE_VENV="$temporary_dir/missing" \
  /bin/bash "$root_dir/scripts/verify-browser-evidence.sh" --install 2>&1)
install_status=$?
set -e

if [[ $install_status -ne 2 ]]; then
  printf 'expected --install to be rejected before provisioning, received %s\n%s\n' \
    "$install_status" "$install_output" >&2
  exit 1
fi
if [[ $install_output != *"expected no argument"* ]]; then
  printf 'expected --install rejection output\n%s\n' "$install_output" >&2
  exit 1
fi
