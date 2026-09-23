# Phase 7–8 Pre-Merge Review Repairs

## Status

`creative-production-phases-7-8` is intentionally **not ready to merge**.
Commits `122af61`, `73e91b5`, `d282f24`, `19e471e`, `2ea2863`, and `e22c6ce`
repair or disclose the first review findings. The focused project verifier passed
9 tests after those changes. A prior full non-live baseline passed 139 tests,
but a fresh full run and independent review are required after every remaining
item below is complete.

## Completed Review Repairs

1. Provider download URLs receive no provider API Authorization header; the
   documented Synthesia `download` field is parsed.
2. An external effect is atomically marked `submitting` before a provider call.
   A restart reconciles by request key or fails closed; it never re-submits an
   ambiguous request. The E2E contract counts submit attempts.
3. Script version 1 is a draft. Deterministic verification creates version 2 as
   the approved revision and downstream work uses that ID.
4. Downloaded bytes are technically inspected before owned storage and asset
   persistence; a non-valid probe cannot reach a ready package.
5. README/limits accurately separate the dry-run control surface, fixture-only
   ready-package path, and unpopulated rights/C2PA evidence.

## Remaining Release Blockers

### Durable Cost Lifecycle

Replace `CreativeWorkflowState`'s in-memory `budget_available_micros` comparison
with a PostgreSQL transaction that creates/idempotently reuses a canonical
budget reservation before provider submission. Link it to `creative_jobs` and
the `external_effect`, record estimated/reserved amounts, settle actual provider
usage on completion, release unused reservation, and verify retry/restart does
not double-reserve or double-settle. Add a budget-exceeds-reservation contract.

### Provider Lifecycle

Persist provider job states for submitted/running/completed/failed/cancelled;
wire configured provider timeout into bounded polling; classify provider errors;
and dead-letter terminal failures. Add a signed webhook ingress that validates
the provider signature, de-duplicates delivery, persists the event/receipt, and
lets polling safely observe an already-completed webhook result. Cancellation
must reconcile the persisted external ID then call `provider.cancel` where the
adapter supports it.

### Capability And Production Agent

Use the persisted Creative Director capability request and
`CreativeCapabilityRegistry` to select a compatible provider. Invoke and record
`production_agent`; bound its variant count with configured quotas; persist each
variant and selected/rejected reason. Remove the workflow’s hard-coded
`text_to_video` request. Add replacement-provider and unsupported-capability
contracts at the workflow boundary.

### Rights And Provenance

Carry explicit asset license/consent/likeness/voice inputs through the workflow,
persist their canonical IDs, record `asset_provenance` including C2PA validation
state, and prohibit finalization when required rights/provenance policy fails.
Keep `not_configured` C2PA as a disclosure fact, not a fabricated valid proof.

### Immutable Distribution Decisions

Make each distribution package’s title/thumbnail/localization/originality and
disclosure decisions immutable after ready-package approval. A new selection
must make a new version and obtain a new final approval; no upsert may mutate a
decision referenced by an existing ready package.

## Required Re-Verification

```bash
bash tests/scripts/test_phase_7_8_documentation.sh
bash scripts/verify-phases-7-8.sh
pytest tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q
python -m compileall -q src
git diff --check
node .agents/skills/archify/bin/archify.mjs validate architecture \
  docs/salience-phase-1-8-final.architecture.json --quality showcase --repo-root . --json
```

Request a new independent review only after the commands above are green. Do not
merge or push to `main` until that review finds no critical or high findings.
