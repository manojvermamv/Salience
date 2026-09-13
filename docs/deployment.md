# Deployment

Run `docker compose --profile application up --build` for PostgreSQL, Temporal,
the API, worker, mock effect provider, and migration job. Set a nonempty
`CONTROL_PLANE_TOKEN` outside source control. The API and worker use service
hostnames; PostgreSQL is canonical and should be backed up/exported with its
Alembic version history through `0005_strategy_idempotency`.

Use a dedicated worker process and restrict network/secret permissions at the
deployment boundary. The mock provider is a test fixture only and must not be
used for production effects.

The worker stays deterministic unless `RESEARCH_RSS_FEED_URLS` contains public
HTTPS feed URLs and `RESEARCH_ALLOWED_DOMAINS` contains their exact hostnames.
The connector does not follow redirects, records untrusted evidence, and fails
closed outside scope. Browser evidence is optional and installed through the
project-owned command:

```bash
bash scripts/verify-browser-evidence.sh --install
```

It measures disk, creates or reuses `.venv`, runs Playwright's official
`install-deps chromium` then `install chromium` path, and executes the local
browser-evidence suite. It installs no Firefox/WebKit, requires no credential,
performs no Docker/system cleanup, and retains local output under
`artifacts/browser-evidence/`.

No model credential, MCP server, A2A endpoint, creative-provider credential,
publisher, or browser binary is required to deploy the Phase 1–9 dry-run loop.
The worker registers `CreativeProductionWorkflow`, but the deployed control
plane fails closed for non-dry creative effects unless an operator explicitly
wires an approved effect configuration. Configure a creative adapter only with
a scope-limited secret reference, selected plugin/provider version, policy,
budget, rights/consent evidence, and compatibility check.

No live publisher is enabled in this release. The verified publication fixture
and control routes use canonical PostgreSQL/Temporal services already in the
Compose topology. The optional YouTube boundary is disabled by default and
requires an operator-managed connection reference, a scoped ephemeral lease,
private visibility, an approved edge media-handoff deployment, current policy,
approval, and budget before it may be enabled. It must run as a separate
least-privileged effect boundary and consume only an approved
`ReadyToPublishPackage@v1`; it cannot inherit creative-provider secrets or
approval as publishing authority.
