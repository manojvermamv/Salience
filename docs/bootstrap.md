# Niche bootstrap

`ContentProgramService.create_from_niche` accepts only a niche, creates a
canonical workspace and content program, invokes the deterministic research and
strategy agent contracts, and persists fixture evidence, scoped evidence and
semantic memory, assumptions, provenance, and an immutable strategy version.

Fixture research is the default clean-deployment connector. It makes no browser
or account request and labels its output as provisional. Production connectors
must implement the same bounded `ResearchConnector` contract and provide their
own policy, consent, provenance, jurisdiction, and retention handling.
