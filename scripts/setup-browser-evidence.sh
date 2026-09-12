#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
venv_dir=${SALIENCE_BROWSER_EVIDENCE_VENV:-"$root_dir/.venv"}
python_bin=${PYTHON:-python3}
browser_cache=${PLAYWRIGHT_BROWSERS_PATH:-"${XDG_CACHE_HOME:-$HOME/.cache}/ms-playwright"}

if ! command -v "$python_bin" >/dev/null 2>&1; then
  printf 'FAIL: Python executable is unavailable: %s\n' "$python_bin" >&2
  exit 1
fi
if ! sudo -n true >/dev/null 2>&1; then
  printf 'FAIL: passwordless sudo is required for Playwright Chromium dependencies.\n' >&2
  exit 1
fi

printf 'OS: %s\n' "$(. /etc/os-release && printf '%s %s' "$NAME" "$VERSION_ID")"
printf 'Disk before: %s\n' "$(df -h "$root_dir" | awk 'NR == 2 {print $4 " free of " $2}')"
printf 'Browser cache: %s\n' "$browser_cache"

if [[ ! -x "$venv_dir/bin/python" ]]; then
  "$python_bin" -m venv "$venv_dir"
fi

export PLAYWRIGHT_BROWSERS_PATH="$browser_cache"
"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install '.[browser,dev]'
sudo -n "$venv_dir/bin/python" -m playwright install-deps chromium
"$venv_dir/bin/python" -m playwright install chromium

browser_version=$("$venv_dir/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        print(browser.version)
    finally:
        browser.close()
PY
)

printf 'Python: %s\n' "$("$venv_dir/bin/python" --version)"
printf 'Playwright: %s\n' "$("$venv_dir/bin/python" -c 'import importlib.metadata; print(importlib.metadata.version("playwright"))')"
printf 'Chromium: %s\n' "$browser_version"
printf 'Disk after: %s\n' "$(df -h "$root_dir" | awk 'NR == 2 {print $4 " free of " $2}')"
printf 'SETUP PASS: Playwright Chromium is ready.\n'
