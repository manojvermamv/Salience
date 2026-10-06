# Task 1 — Root README V4 architecture report

## Implementation

Replaced the root README’s stale checkpoint narrative with a V4 status overview that distinguishes the application qualification, the published feature/documentation checkpoint, and the CI readback for that checkpoint. The README records the exact requested application and documentation hashes, focused/full test counts, source-hash count, CI run and warning count, production holds, phase gates, and main-branch state.

Added two GitHub-compatible Mermaid flowcharts: a high-level target architecture with textual local/held/planned/optional statuses, and a detailed no-effects governed execution path covering signed and scheduled ingress, current authorization, atomic canonical admission/outbox, ordered token-fenced delivery, durable inbox receipts, compatible Temporal workflows, bounded stages, per-stage checks, canonical records, traceability, and rollback boundaries. Green status is explicitly scoped to local fixtures, not production readiness.

Retained the existing verification commands, navigation content, historical Phase 1–9 link, and all 16 historical HTML heading anchors. Updated only the root `README.md` SHA-256 value in `docs/v4/documentation-status-audit.json`; its self-entry remains null. No application code or other narrative document changed.

## Evidence and status boundaries

- Application fixture qualification: `a4b2368cdb463939d093020224a5b5667392aee6`, 21 focused / 604 full non-live tests, 352 source hashes. This covers the specified original legacy stages and both original native schedule conversions in a no-effects fixture with zero publication budget.
- Published feature/documentation checkpoint: `a2e73c7242d67388e8cd3be4361283cd8e6c081a`.
- Exact checkpoint CI readback: run `37346137016`; `verify` and `p0-regression` passed, with 604 passed and three warnings in `p0-regression`.
- Exact corrected-head publication rereview remains pending. No missing rereview artifact is represented as existing.
- Production effects remain disabled; RG0/RG1 are HELD; P2–P7 are gated/planned; optional P6 is disabled; main remains at `8e50d68efbf2bec2aabfb2fe3226e01635de2adb` and unmerged.

The CI readback above is bound to the published checkpoint. This local README follow-up has no published-head CI readback yet.

## Verification

- `python3 docs/v4/test_checks.py` — **15 tests passed**.
- `python3 docs/v4/check.py --self-test` — **PASS**; 75 requirements, 2 source files, 352 evidence files, 360 inventory files, 85 Markdown files, 4 HTML files, and 6 negative controls.
- `python3 docs/v4/check.py` — **PASS**; same consolidated documentation and link checks, no errors.
- Mermaid 12.0.0 with the existing local Playwright/headless Chromium installation — **both README flowcharts parsed and rendered**. No dependencies were installed.
- `git diff --check` — **PASS**.
- Manual scope review — only README, its audit SHA entry, and this requested report are included; all 16 prior README anchors remain; the audit hash matches the final README bytes and its self-entry remains null.

Application regression tests were not rerun because this change only updates documentation and the requested documentation, link, and diagram checks passed.

## Self-review

The README states all requested current-status anchors and keeps application evidence, published documentation HEAD, and CI readback separate. Both diagrams include the requested governance, data, delivery, workflow, stage, effects, later-phase, operations, and recovery boundaries. Every status color has a corresponding textual label, and nearby prose says local fixture qualification does not imply production readiness. The status table and plain-language release explanation preserve the held production gates. Required authoritative links resolve under the documentation validator. Historical anchors and useful verification/navigation content remain available.

Remaining follow-up is external: publish the documentation commit and read back CI for its exact new head. The current successful CI evidence applies only to `a2e73c7242d67388e8cd3be4361283cd8e6c081a`.
