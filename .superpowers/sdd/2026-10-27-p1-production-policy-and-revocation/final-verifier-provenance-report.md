# Final verifier provenance report

## Changes

- The Phase 9 verifier now checks tracked and untracked repository changes at startup using Git’s normal ignore rules, resolves and validates the full 40-character `HEAD`, and prints `SOURCE_COMMIT: <sha>` before documentation checks, service startup, migrations, and tests.
- Dirty source state, an unavailable Git status, an unresolvable `HEAD`, or malformed commit output exits before the disposable database can be created. Existing cleanup behavior remains unchanged.
- Extended the focused verifier shell contract to prove exact commit output on a clean source tree; rejection of tracked and untracked changes and malformed commit output before database create/drop; and preservation of existing failed-create and failed-cleanup behavior.

## TDD and verification

- RED: the new clean-source assertion failed because the verifier did not print `SOURCE_COMMIT`.
- `bash tests/scripts/test_verify_phase_9_cleanup.sh`: passed.
- `bash tests/scripts/test_phase_9_documentation.sh`: passed.
- `bash -n scripts/verify-phase-9.sh tests/scripts/test_verify_phase_9_cleanup.sh`: passed.
- `git diff --check`: passed.

The full Phase 9 verifier was not run. No evidence documents were changed.
