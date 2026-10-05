# Documentation

**Current checkpoint — 2026-10-05:** Resumed P1 intelligence, selected briefs and native Temporal schedule conversion are locally qualified (**98 focused / 567 full non-live PASS**, 338 source hashes) at `10eb77f`. Native schedule UUIDs and row identity are preserved; authenticated source mapping, actual progress, physical drain, rollback fencing and original-slot admission/job/outbox are qualified. See the [legacy integration contract](v4/legacy-dispatch-contract.md). Item 7 and the parallel extension retain their completed, dated scopes. The broader P1 integration goal remains active; production is disabled, RG0/RG1 HELD, main unmerged, P2–P7 gated and optional P6 disabled.

Use the [V4 production implementation blueprint](v4/IMPLEMENTATION-BLUEPRINT.md) for all future work. It contains the verified starting point, complete target intent, phases, requirement mappings, acceptance, migrations, rollback, release gates and operating procedures.

[Validation evidence](v4/validation.md), [current progress and checkpoints](v4/progress.md), [release ledger](v4/p0-release.json), [inventory](v4/inventory.json) and [archive](archive/2026-09-22/README.md) support that blueprint. Former guide, core-pack and Superpowers paths are navigation aliases. They no longer define competing execution plans.

P0 and DG1 remain locally qualified. P1 items 1–6 are implemented in no-effects fixtures; item 7 independently qualified and repaired that implementation on 2026-10-04. The original item-6 85/456 and preparation 32/462 reports remain dated snapshots. Follow the current evidence and remaining RG0/RG1 acceptance below.

The latest local implementation is the [bounded parallel-agent extension](v4/parallel-agent-contract.md), with its own [qualification evidence](v4/parallel-agents-evidence.json) and source lock. It adds independent concurrent children, canonical parent/child lineage, durable single-attempt worker claims/results, signed API/CLI/SDK parity and recovery of queued work. The original item-7 132/507 qualification remains a dated independent review of `e49a214`. Production effects remain disabled, RG0/RG1 HELD, main unmerged and later phases gated.

Reuse [completed work](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) and the [ordered queue](v4/IMPLEMENTATION-BLUEPRINT.md#p1-resume-queue). The feature branch is `origin/feat/archv4-p0-foundation`, with local branch `arch`; the final documentation head must receive its own required CI/protection inspection after publication. Formal GitHub PR approval and separate authorization still gate any merge.

Historical Archify JSON/HTML and screenshots retain their original bytes and implementation scope. They do not represent a deployed V4 system.
