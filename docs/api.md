# Control API

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**Current checkpoint — 2026-10-05:** Bounded parallel agent delegation is locally qualified (**45 focused / 540 full non-live PASS**, 325 source hashes) at `2653d2e`: one shared parent, scoped children, bounded concurrency/deadlines, durable claims/results and signed API/CLI/SDK commands. See the [parallel-agent contract](v4/parallel-agent-contract.md) for invocation, recovery and qualification. The completed 2026-10-04 item-7 independent review remains a dated qualification of its original source. Production stays disabled, RG0/RG1 HELD, main unmerged and P2–P7 gated.

[Original historical evidence](<archive/2026-09-22/docs/api.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Existing `CreativeProductionRequest@v1` and private publication contracts are summarized in the blueprint evidence table; production subject/account authorization is still required.

Signed local fixture commands retain API/CLI/SDK parity. Item 6 adds `GET /v1/v4/intents/{intent_id}`, SDK `cycles.inspect_intent` and CLI `cycles intent-inspect` under current `cycles:read`; revoked write authority never becomes execution permission. Case inspection exposes original owner, bounded waits and notification delivery/ack state; cycle inspection exposes its immutable runtime hold. See the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract).

<!-- Historical heading anchors retained for incoming links. -->
<a id="control-api"></a>
<a id="creative-production"></a>
<a id="governed-publication"></a>
