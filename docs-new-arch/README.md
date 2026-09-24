# Architecture design sources

The [V4 production implementation blueprint](../docs/v4/IMPLEMENTATION-BLUEPRINT.md) is the execution authority. It incorporates both complete V4 sources and grounds them in inspected code, tests and configuration.

Implementation status is maintained in the blueprint's [current snapshot](../docs/v4/IMPLEMENTATION-BLUEPRINT.md#current-execution), [progress/checkpoints](../docs/v4/progress.md#current-state--2026-09-24) and [release ledger](../docs/v4/p0-release.json). P0 is locally qualified, P1 has verified local increments but remains incomplete, and P2–P7 remain planned. Original ArchV4 text/diagrams describe target intent and are not rewritten to imply deployed capabilities; historical `ArchCurrent` and Archify visuals are not current V4 completion evidence.

- [V4 contracts](ArchV4-implementation-notes.md) and [V4 overview](ArchV4.mermaid) are preserved source intent, not deployment certification.
- [V3 notes](ArchV3-implementation-notes.md), [V3](ArchV3.mermaid), [V2](ArchV2.mermaid), [V1](ArchV1.mermaid) and [prior current-state sketch](ArchCurrent.mermaid) are historical design artifacts superseded by V4.

There is no `ArchV4.svg` in this repository. The blueprint embeds the Mermaid overview and focused diagrams directly. Update source and blueprint coverage together when an approved design change occurs.
