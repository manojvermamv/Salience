# Documentation

Use the [V4 production implementation blueprint](v4/IMPLEMENTATION-BLUEPRINT.md) for all future work. It contains the verified starting point, complete target intent, phases, requirement mappings, acceptance, migrations, rollback, release gates and operating procedures.

[Validation evidence](v4/validation.md), [current progress and checkpoints](v4/progress.md), [release ledger](v4/p0-release.json), [inventory](v4/inventory.json) and [archive](archive/2026-09-22/README.md) support that blueprint. Former guide, core-pack and Superpowers paths are navigation aliases. They no longer define competing execution plans.

As of 2026-09-24, P0 is locally qualified and DG1 passes. P1 admission/outbox, goal revisions and explicit fixture baseline approval are implemented locally; full P1 remains ongoing and RG1 HELD. P2–P7 are planned, not completed by the older Phase 1–9 fixtures. Production effects remain disabled and RG0 is HELD. The latest application qualification is 317 passing non-live tests from 2026-09-23, not a fresh application run during this documentation refresh.

For handoff, read the blueprint's [completed work](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) and [ordered remaining P1 work](v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). Current source is on `feat/archv4-p0-foundation`; main is unmerged. Required checks and production obligations are separate gates.

Historical Archify JSON/HTML and screenshots retain their original bytes and implementation scope. They do not represent a deployed V4 system.
