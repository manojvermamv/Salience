# Deployment

Run `docker compose --profile application up --build` for PostgreSQL, Temporal,
the API, worker, mock effect provider, and migration job. Set a nonempty
`CONTROL_PLANE_TOKEN` outside source control. The API and worker use service
hostnames; PostgreSQL is canonical and should be backed up/exported with its
Alembic version history.

Use a dedicated worker process and restrict network/secret permissions at the
deployment boundary. The mock provider is a test fixture only and must not be
used for production effects.

The worker stays deterministic unless `RESEARCH_RSS_FEED_URLS` contains public
HTTPS feed URLs and `RESEARCH_ALLOWED_DOMAINS` contains their exact hostnames.
The connector does not follow redirects, records untrusted evidence, and fails
closed outside scope. Browser setup is optional: `pip install '.[browser]'` then
`playwright install chromium`, only after checking available disk capacity.
