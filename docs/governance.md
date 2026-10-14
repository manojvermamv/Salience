# Governance Controls



**Historical scope:** This superseded guide preserves earlier design, evidence, or navigation; its checkpoint is not current project status. Use [V4 progress](v4/progress.md#current-state) and [the V4 blueprint](v4/IMPLEMENTATION-BLUEPRINT.md#current-execution) for the active handoff.

[Original historical evidence](<archive/2026-09-22/docs/governance.md.snapshot>) is retained with its inventory hash. It is superseded source text, not a current execution plan.

<!-- Historical heading anchors retained for incoming links. -->
<a id="governance-controls"></a>
<a id="authorization"></a>
<a id="budgets-and-costs"></a>
<a id="creative-gates"></a>
<a id="secrets-and-permissions"></a>
<a id="tracing"></a>
<a id="future-hooks"></a>
<a id="publisher-gates"></a>

The isolated legacy publication path binds one fixture account to the exact approved goal revision/baseline and requires the current `legacy:publication`, `legacy:publication:account:<UUID>` and, separately, `legacy:publication:reconcile:account:<UUID>` scopes. Native profile, approval, disclosure, policy, rights and zero-budget facts are rechecked at the relevant boundary. This is a no-effects fixture contract; general production provider/account eligibility remains HELD. See the [publication contract](v4/legacy-dispatch-contract.md#governed-publication-stage).
