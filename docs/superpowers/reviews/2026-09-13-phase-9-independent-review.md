# Phase 9 Governance Self-Audit

**Date:** 2026-09-13

**Scope:** Repairs after the independent review of the governed publishing
boundary, including the additive `0012_publication_profile_scope` migration.

## Executable evidence

- `bash scripts/verify-phase-9.sh` passed 58 focused fixture tests and the
  complete 232-test non-live suite.
- The verifier passed migration application through `0012`, Python compilation,
  whitespace integrity, Archify composition validation (9/9, zero diagnostics),
  and the YouTube live-status contract (`NOT RUN` without a configured private
  connection reference).
- The current host has no Chrome/Chromium executable; the requested visual
  containment/readability capture is therefore explicitly `NOT RUN`.

## Finding closure audit

| Reviewed concern | Local evidence |
| --- | --- |
| Publication authority was confused with creative approval | Request creation requires a distinct approved `effect_type = 'publication'` row and exact package/account/platform/destination/locale/territory/visibility/profile context. |
| Policy and rights scope could be empty or mismatched | Current authorization requires active workspace policy references with exact publication scope, package assets, and an active commercial rights path permitting both platform and territory. |
| Connection/profile facts could be stale or cross-account | Authorization reloads connection expiry/revocation and an account- and platform-scoped verified, unexpired capability profile selected by the immutable request. |
| Scheduled runs did not materialize canonical jobs | Temporal schedule payloads carry only the canonical schedule identity; the request activity loads the immutable request/plan/budget and creates one canonical run keyed by Temporal execution run ID. |
| Schedule payload fields were not exact-matched | Canonical schedule creation rejects an existing name whose request, plan, budget, version, or contract payload differs. |
| Invalid normal starts left orphan jobs | Production control persists and validates the canonical request before creating a running job or connecting to Temporal. |
| Approved scope drift could fail open | Current authorization returns `invalid_scope` for an approved row whose context no longer exactly matches the immutable request. |

## Review-service limitation

Two fresh independent-review requests were attempted through the available
reviewer agents. Both failed before analysis because the external reviewer
service reported its usage limit. This document is a local self-audit, not a
claim of independent approval. Integration must retain that limitation in its
release notes until a reviewer service is available.
