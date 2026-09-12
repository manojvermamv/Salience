# Deployment

Run `docker compose --profile application up --build` for PostgreSQL, Temporal,
the API, worker, mock effect provider, and migration job. Set a nonempty
`CONTROL_PLANE_TOKEN` outside source control. The API and worker use service
hostnames; PostgreSQL is canonical and should be backed up/exported with its
Alembic version history.

Use a dedicated worker process and restrict network/secret permissions at the
deployment boundary. The mock provider is a test fixture only and must not be
used for production effects.
