# Final Local Verification Evidence — 2026-10-27

The final local non-live verifier passed for application/source candidate `ec12763e38143f52f1af2661e00932158982f11a` at migration head `0046_production_policy_approval_id_immutability`. The verifier log begins with `SOURCE_COMMIT: ec12763e38143f52f1af2661e00932158982f11a` and is SHA-256 bound below. The documentation commit carrying this record is later and distinct from the verified application candidate.

Command: `bash scripts/verify-phase-9.sh`

The Phase 9 fixture release gate passed 60 tests in 55.11 seconds. The full non-live suite passed 908 tests, skipped 26, and emitted 3 warnings in 1438.43 seconds. Python compilation, `git diff --check`, Phase 1–9 Archify validation, and disposable database cleanup passed. The verifier exited 0.

The live YouTube provider smoke was NOT RUN because `YOUTUBE_PUBLISHER_CONNECTION_REF` was not configured. The YouTube live-status contract test passed 1 test. The run produced no application image and no JUnit artifact. Connection references, credentials, endpoints, and database URLs are omitted from the evidence.

The sanitized verifier log is `/tmp/salience-final-ec12763-verify.log`, SHA-256 `c643740f100210bad8428433db31ecd2852a538b26ad702224cd28c3f5432020`. A byte-identical durable copy is [retained with the audit evidence](../../../docs/v4/audits/p1-policy-revocation-ec12763-verifier.log). The structured evidence is [p1-production-policy-revocation-evidence.json](../../../docs/v4/p1-production-policy-revocation-evidence.json), SHA-256 `e5a5a3d424157479cacaf00eae39614a5abe12ecdb04ac38af0c95692dbd982b`.

The frozen 0043 artifact candidate `182c4e43d3666a7c21c22d4d8fa3e8c994e6ce7f` and its 796 passed / 17 skipped / 3 warnings remain historical evidence. The prior Task 6 focused fixture result at `3cd1e28` / `0045_production_policy_approval_guard_repair` is preserved as historical evidence.

Production target configuration and acceptance were NOT RUN. Production effects remain disabled; RG0 and full RG1 remain HELD; P2 remains BLOCKED; `main` remains unmerged. These local fixture results do not establish production acceptance.

## Focused documentation checks

`.venv/bin/python docs/v4/test_checks.py` passed 24 tests. `bash tests/scripts/test_phase_9_documentation.sh` passed. `git diff --check` passed.
