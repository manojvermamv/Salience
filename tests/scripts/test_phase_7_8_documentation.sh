#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"

require_text() {
  local text=$1
  local path=$2
  grep -Fq "$text" "$path" || {
    printf 'Missing %s in %s\n' "$text" "$path" >&2
    exit 1
  }
}

require_file() {
  [[ -f $1 ]] || {
    printf 'Missing required file %s\n' "$1" >&2
    exit 1
  }
}

require_file docs/phase-9-handoff.md
require_file scripts/verify-phases-7-8.sh
require_file tests/e2e/test_phase7_8_release_gate.py
require_text 'ReadyToPublishPackage@v1' docs/phase-9-handoff.md
require_text 'only valid Phase-9 publishing input' docs/phase-9-handoff.md
require_text 'bash scripts/verify-phases-7-8.sh' README.md
require_text 'bash scripts/verify-phases-7-8.sh' docs/verification.md
require_text 'NOT RUN' scripts/verify-phases-7-8.sh
require_text '0008_creative_release_gate' scripts/verify-phases-7-8.sh
require_text 'test_phase7_8_release_gate.py' scripts/verify-phases-7-8.sh
require_text 'durable reservation/actual settlement' docs/verification.md
require_text 'verified webhook deduplication' docs/verification.md
require_text 'new distribution and ready-package version' README.md
require_text 'triggers that reject direct mutation' docs/database.md
require_text 'explicit budget reservations and actual-cost settlement' docs/limitations.md
require_text 'CreativeProductionRequest@v1' docs/api.md
require_text 'no live social publishing' docs/architecture.md
require_text 'CreativeService' docs/architecture.md
require_text 'Phase 1–8' docs/salience-phase-1-8-final.architecture.json

printf 'Phase 7–8 documentation contract passed\n'
