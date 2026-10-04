# Architecture design sources

Current qualification: item 7 independent implemented-fixture review is complete at application `e49a214`, **132 focused / 507 full non-live PASS**, 316 source hashes and all 17 P1 rows assessed. [Evidence](../docs/v4/item7-evidence.json) and [current progress](../docs/v4/progress.md#current-state) retain reviewer conclusions and remaining acceptance. Items 1–6 reports remain dated snapshots. Production effects remain disabled, RG0/full RG1 HELD, main unmerged and later phases gated.

The [V4 production implementation blueprint](../docs/v4/IMPLEMENTATION-BLUEPRINT.md) is the execution authority. It incorporates both complete V4 sources and grounds them in inspected code, tests and configuration.

Implementation status is maintained in the blueprint’s [current snapshot](../docs/v4/IMPLEMENTATION-BLUEPRINT.md#current-execution), [progress](../docs/v4/progress.md#current-state) and [release ledger](../docs/v4/p0-release.json). P0 and P1 items 1–6 remain locally implemented; item 7 independent fixture qualification is complete at application `e49a214`, with 132 focused / 507 full non-live PASS, 316 source hashes and all 17 P1 rows reviewed. [Evidence](../docs/v4/item7-evidence.json) retains the independent reports and remaining production obligations. RG0/full RG1 remain HELD, effects disabled, main unmerged and P2–P7 gated. Original ArchV4 text/diagrams retain their source hashes and target intent; historical ArchCurrent and Archify visuals retain their dated scope.

- [V4 contracts](ArchV4-implementation-notes.md) and [V4 overview](ArchV4.mermaid) are preserved source intent, not deployment certification.
- [V3 notes](ArchV3-implementation-notes.md), [V3](ArchV3.mermaid), [V2](ArchV2.mermaid), [V1](ArchV1.mermaid) and [prior current-state sketch](ArchCurrent.mermaid) are historical design artifacts superseded by V4.

There is no `ArchV4.svg` in this repository. The blueprint embeds the Mermaid overview and focused diagrams directly. Update source and blueprint coverage together when an approved design change occurs.
