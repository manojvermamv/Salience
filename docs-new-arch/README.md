# Architecture design sources

The [V4 production implementation blueprint](../docs/v4/IMPLEMENTATION-BLUEPRINT.md) is the execution authority. It incorporates both complete V4 sources and grounds them in inspected code, tests and configuration.

Implementation status is maintained in the blueprint's [current snapshot](../docs/v4/IMPLEMENTATION-BLUEPRINT.md#current-execution), [progress/checkpoints](../docs/v4/progress.md#current-state) and [release ledger](../docs/v4/p0-release.json). P0 and P1 items 1–6 are locally qualified at `e5d18401255810c74b85b2d8e57daca8cda8d9a3`, with **85 focused / 456 full non-live PASS** dated 2026-10-03 and both required CI/protection checks PASS. Item 6 completes compatible history replay, bounded Continue-As-New, finite waits/review deadlines, owner holds, no-effects database notification receipts and local restart/restore qualification. Continue at item 7 independent full RG1 qualification and outstanding production acceptance. P2–P7 remain planned, production effects disabled and RG0/full RG1 HELD. Original ArchV4 text/diagrams describe target intent and retain their source hashes; historical `ArchCurrent` and Archify visuals retain their original scope. [Documentation audit](../docs/v4/validation.md#repository-wide-documentation-refresh--2026-10-03) records the preserved sources.

- [V4 contracts](ArchV4-implementation-notes.md) and [V4 overview](ArchV4.mermaid) are preserved source intent, not deployment certification.
- [V3 notes](ArchV3-implementation-notes.md), [V3](ArchV3.mermaid), [V2](ArchV2.mermaid), [V1](ArchV1.mermaid) and [prior current-state sketch](ArchCurrent.mermaid) are historical design artifacts superseded by V4.

There is no `ArchV4.svg` in this repository. The blueprint embeds the Mermaid overview and focused diagrams directly. Update source and blueprint coverage together when an approved design change occurs.
