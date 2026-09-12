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
