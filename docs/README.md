# Documentation

Use the [V4 production implementation blueprint](v4/IMPLEMENTATION-BLUEPRINT.md) for all future work. It contains the verified starting point, complete target intent, phases, requirement mappings, acceptance, migrations, rollback, release gates and operating procedures.

[Validation evidence](v4/validation.md), [current progress and checkpoints](v4/progress.md), [release ledger](v4/p0-release.json), [inventory](v4/inventory.json) and [archive](archive/2026-09-22/README.md) support that blueprint. Former guide, core-pack and Superpowers paths are navigation aliases. They no longer define competing execution plans.

P0 and DG1 remain locally qualified. P1 items 1–6 are implemented in no-effects fixtures; item 7 independently qualified and repaired that implementation on 2026-10-04. The original item-6 85/456 and preparation 32/462 reports remain dated snapshots. Follow the current evidence and remaining RG0/RG1 acceptance below.

Current qualification: item 7 independent implemented-fixture review is complete at application `e49a214`, **132 focused / 507 full non-live PASS**, 316 source hashes and all 17 P1 rows assessed. [Evidence](v4/item7-evidence.json) and [current progress](v4/progress.md#current-state) retain reviewer conclusions and remaining acceptance. Items 1–6 reports remain dated snapshots. Production effects remain disabled, RG0/full RG1 HELD, main unmerged and later phases gated.

Reuse [completed work](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) and the [ordered queue](v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). The feature branch is `origin/feat/archv4-p0-foundation`, with local branch `arch`; the final documentation head must receive its own required CI/protection inspection after publication. Formal GitHub PR approval and separate authorization still gate any merge.

Historical Archify JSON/HTML and screenshots retain their original bytes and implementation scope. They do not represent a deployed V4 system.
