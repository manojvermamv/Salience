# Research Operations

Salience uses `official API/feed -> direct HTTP -> maintained connector -> governed browser`.
RSS/Atom and Hacker News are read-only source adapters; each result retains resource
identity, URL, hash, timestamps, feature availability, rate-limit metadata, and provenance.

Set `RESEARCH_RSS_FEED_URLS` and exact `RESEARCH_ALLOWED_DOMAINS` to make the worker's
Research Agent consume public feeds. Redirects, non-HTTPS hosts, oversized/non-XML feeds,
and scope violations fail before canonical evidence is written. Source content is always
`untrusted_external`, never instruction.

The durable intelligence loop normalizes retained evidence into explicit
signals, scores source-supported opportunities, obtains provider-neutral
research/strategy outputs, and persists strategy assumptions before generating
and evaluating strategic packages. A selected package can produce a brief only
from verified, non-contradicted claim evidence. The outcome is an immutable
`ContentBrief@v1`, not a publishable asset.

`BrowserResearchTool` is an optional Playwright adapter with an isolated context, approved
network routes, download denial, bounded work, and object-store text/screenshot/trace artifacts.
It must not bypass CAPTCHA, paywalls, access controls, or platform restrictions. Normal CI uses
fixtures; real network smoke requires `LIVE_HACKER_NEWS_ITEM_URL`.
