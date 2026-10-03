# Control API

Superseded by the [V4 production implementation blueprint](<v4/IMPLEMENTATION-BLUEPRINT.md>). Use that document for all future work; this page preserves navigation only.

**V4 checkpoint — 2026-10-03:** P0 and P1 items 1–6 are locally qualified in no-effects fixtures at `e5d1840`: **85 focused / 456 full non-live PASS**, with both required CI checks and strict main protection PASS. Use [current progress](v4/progress.md#current-state), [completed work](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) and [validation](v4/validation.md#current-evidence-summary). Next is **item 7**, independent full RG1 qualification and outstanding production acceptance. RG0/full RG1 remain HELD, effects disabled, main unmerged and P2–P7 planned. Historical Phase 1–9 evidence below retains its original scope.

[Original historical evidence](<archive/2026-09-22/docs/api.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

Existing `CreativeProductionRequest@v1` and private publication contracts are summarized in the blueprint evidence table; production subject/account authorization is still required.

Signed local fixture commands retain API/CLI/SDK parity. Item 6 adds `GET /v1/v4/intents/{intent_id}`, SDK `cycles.inspect_intent` and CLI `cycles intent-inspect` under current `cycles:read`; revoked write authority never becomes execution permission. Case inspection exposes original owner, bounded waits and notification delivery/ack state; cycle inspection exposes its immutable runtime hold. See the [runtime/wait contract](v4/IMPLEMENTATION-BLUEPRINT.md#p1-item-6-local-runtime-and-wait-contract).

<!-- Historical heading anchors retained for incoming links. -->
<a id="control-api"></a>
<a id="creative-production"></a>
<a id="governed-publication"></a>
