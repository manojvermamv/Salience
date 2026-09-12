# Local development

Copy `.env.example` to a local `.env` only when overriding defaults. Do not
commit it. Start the application profile with:

```bash
docker compose --profile application up --build
docker compose run --rm migrate
```

The worker consumes `salience-phase-one`; the API creates dry-run workflows on
that queue. PostgreSQL is canonical, while Temporal and Garage remain replaceable
adapters behind project-owned contracts.
