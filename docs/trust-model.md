# Trust Model

Web pages, feeds, comments, documents, browser DOM, remote tools, and remote-agent artifacts
are `untrusted_external`: they are data, not system instructions. `TrustContext` carries source
identity, effect classification, delegated authority, tool/network scopes, memory-write authority,
provenance, and trace identity.

Untrusted input receives no delegated authority or write-tool access. It cannot select a model or
policy, expand a scope, mark itself verified, or directly create trusted durable memory.
`TrustPolicy` validates memory writes; external research is forced to source-linked, unverified
evidence memory.

Claims and evidence are separate. A claim is verified only with explicitly verified supporting
evidence and no contradiction. Unverified or contradictory claims become prohibited brief claims,
not facts.

The durable workflow keeps this classification across checkpoints and agent
delegations. Research and browser outputs retain source-linked findings,
questions, and contradictions; Strategy outputs retain assumptions and
uncertainties. A model adjustment is bounded supplemental input to deterministic
ranking and can never replace source facts or verify a claim.
