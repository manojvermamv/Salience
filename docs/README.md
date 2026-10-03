# Documentation

Use the [V4 production implementation blueprint](v4/IMPLEMENTATION-BLUEPRINT.md) for all future work. It contains the verified starting point, complete target intent, phases, requirement mappings, acceptance, migrations, rollback, release gates and operating procedures.

[Validation evidence](v4/validation.md), [current progress and checkpoints](v4/progress.md), [release ledger](v4/p0-release.json), [inventory](v4/inventory.json) and [archive](archive/2026-09-22/README.md) support that blueprint. Former guide, core-pack and Superpowers paths are navigation aliases. They no longer define competing execution plans.

As of 2026-10-03, P0 is locally qualified and DG1 passes. P1 items 1–6 are implemented in no-effects fixtures: prior admission/policy/accounting/governance/commands/cutover plus compatible workflow replay, bounded Continue-As-New, finite durable waits/deadlines and PostgreSQL fixture notification receipts. Item 6 has **85 focused / 456 full non-live PASS**; publication and exact-head CI are separate recorded steps. [Initial item-6 evidence](v4/item6-evidence.json) and [final operator correction](v4/item6-operator-evidence.json) and [handoff](v4/progress.md#item-6-resume-handoff) retain the exact scope. Continue at item 7 independent full RG1 qualification, with outstanding production acceptance preserved. RG0/full RG1 remain HELD; effects disabled, main unmerged and P2–P7 planned.

Read [completed work](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) and the [ordered remaining queue](v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). The qualification base is published documentation `e30a2f6` on `origin/feat/archv4-p0-foundation`; local branch `arch`. Every new published HEAD requires its own required CI/protection inspection. Historical repository evidence binds its stated commit only; current independent approval and separate authorization still gate any merge.

Historical Archify JSON/HTML and screenshots retain their original bytes and implementation scope. They do not represent a deployed V4 system.
