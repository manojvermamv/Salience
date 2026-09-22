#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
venv_dir=${SALIENCE_BROWSER_EVIDENCE_VENV:-"$root_dir/.venv"}

case ${1:-} in
  '') ;;
  *)
    printf 'NOT RUN: expected no argument.\n' >&2
    exit 2
    ;;
esac

if [[ ! -x "$venv_dir/bin/python" ]]; then
  printf 'NOT RUN: Playwright environment is absent; provision it outside Salience before running this verifier.\n' >&2
  exit 2
fi

export PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-"${XDG_CACHE_HOME:-$HOME/.cache}/ms-playwright"}
python_bin="$venv_dir/bin/python"
if ! browser_version=$("$python_bin" - <<'PY' 2>/dev/null
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        print(browser.version)
    finally:
        browser.close()
PY
); then
  printf 'NOT RUN: Playwright Chromium is unavailable; provision it outside Salience before running this verifier.\n' >&2
  exit 2
fi

evidence_dir=${SALIENCE_BROWSER_EVIDENCE_DIR:-"$root_dir/artifacts/browser-evidence/$(date -u +%Y%m%dT%H%M%SZ)-$$"}
mkdir -p "$evidence_dir"
export SALIENCE_BROWSER_EVIDENCE_DIR="$evidence_dir"

printf 'Playwright: %s\n' "$("$python_bin" -c 'import importlib.metadata; print(importlib.metadata.version("playwright"))')"
printf 'Chromium: %s\n' "$browser_version"
printf 'Evidence directory: %s\n' "$evidence_dir"

if "$python_bin" -m pytest tests/integration/test_browser_evidence.py -m browser -v; then
  printf 'PASS: browser evidence verification completed.\n'
else
  status=$?
  printf 'FAIL: browser evidence verification failed; inspect %s\n' "$evidence_dir" >&2
  exit "$status"
fi
