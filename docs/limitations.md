# Current limits

Phases 1–8 provide a deterministic substrate, SDK compatibility adapters,
bounded read-only research, source-linked intelligence records, strategic
packages, claim/evidence checks, immutable ContentBriefs, versioned scripts and
creative direction, deterministic fixture-provider media flow, canonical asset
lineage plus rights/provenance schema and policy hooks, governed distribution packages, and immutable approved
ready-package handoffs. They do not implement live social publishing, analytics,
experimentation, learning, or a general crawler.

Browser evidence is optional to the control plane but is verified on this Debian
13 host with `playwright==1.62.0` and Playwright Chromium 151.0.7922.34. It is a
read-only evidence collector, not a general crawler: it permits exact HTTPS
allowlisted domains, blocks private/localhost IP literals by default, disables
downloads, and treats retrieved content as `untrusted_external`. The local
integration fixture is the only explicit loopback/self-signed-certificate
exception. Real RSS feeds are opt-in and allowlisted; fixtures remain CI
defaults. No model credentials are supplied or required. Garage deployment
configuration and a production secret manager remain adapter/operator work. The
data model preserves tenant,
classification, retention, jurisdiction, provenance/C2PA, trust/delegation, and
OpenTelemetry compatibility hooks for later deployment policy.

The implemented production loop ends at `ReadyToPublishPackage@v1`. The verified
provider path is a dedicated deterministic test fixture; the deployed control
plane defaults to dry-run and has no ready-package-producing effect setup. A
credential-gated provider adapter exists only as a disabled boundary.
The fixture release gate does not execute host FFmpeg media composition or C2PA
signing, even if their binaries or signer configuration are present; these checks
are explicitly `NOT RUN`, not claimed as production-verified. Rights/consent and
asset-provenance records are canonical schema and policy boundaries; the fixture
proves persisted rights reload and generated provenance but does not populate
real likeness, voice, or C2PA signer evidence. Non-dry creative variants do
persist explicit budget reservations and actual-cost settlement, but real
provider pricing/reconciliation remains adapter-specific. Cancellation and
signed-webhook ingress are implemented against fixture/provider contracts, not
as a configured live-provider deployment. A final package grants no authority
to publish, purchase, expand scopes, or bypass future publisher policy. See
[`docs/phase-9-handoff.md`](phase-9-handoff.md).
