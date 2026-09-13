#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"

require_file() {
  [[ -f $1 ]] || {
    printf 'Missing required file %s\n' "$1" >&2
    exit 1
  }
}

require_text() {
  local text=$1
  local path=$2
  grep -Fq "$text" "$path" || {
    printf 'Missing %s in %s\n' "$text" "$path" >&2
    exit 1
  }
}

require_file scripts/verify-phase-9.sh
require_file docs/adr/0006-governed-publishing.md
require_file docs/salience-phase-1-9.architecture.json
require_file docs/salience-phase-1-9.architecture.html
require_text 'bash scripts/verify-phase-9.sh' README.md
require_text 'Phase 1–9' README.md
require_text '0009_governed_publication' docs/database.md
require_text 'disabled by default' docs/dependencies.md
require_text 'YouTubePublisherAdapter' docs/architecture.md
require_text 'private' docs/api.md
require_text 'NOT RUN' docs/verification.md
require_text 'does not publish a video' docs/limitations.md
require_text 'official YouTube Data API' docs/adr/0006-governed-publishing.md
require_text 'Phase 1–9' docs/salience-phase-1-9.architecture.json

printf 'Phase 9 documentation contract passed\n'
