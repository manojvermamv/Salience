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

`BrowserResearchTool` is an optional Playwright adapter with a fresh headless context per
run, exact HTTPS/domain routes, private-address denial, download denial, request/timeout/text
bounds, and object-store text/screenshot/trace artifacts. Each receipt records a source URL,
fetch time, Playwright/Chromium versions, agent/tool/trace IDs, artifact type, and SHA-256 hash.
Browser text is `untrusted_external` evidence, not instruction, authority, or verified memory.

Run `bash scripts/verify-browser-evidence.sh --install` to install only the pinned Playwright
Chromium runtime/dependencies and execute the self-contained HTTPS fixture suite. It needs no
credential or public network source and leaves the PNG, trace ZIP, text, and JSON receipts under
`artifacts/browser-evidence/`. The fixture alone explicitly permits loopback and its self-signed
certificate; production callers retain private-network and certificate checks. The adapter must
not bypass CAPTCHA, paywalls, access controls, or platform restrictions. Normal CI uses fixtures;
real network smoke requires `LIVE_HACKER_NEWS_ITEM_URL`.
