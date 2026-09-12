#!/usr/bin/env bash
set -euo pipefail

project_name="salience-verify-$(date +%s)-$RANDOM"
cleanup() {
  docker compose --project-name "$project_name" down --volumes --remove-orphans
}
trap cleanup EXIT

docker compose --project-name "$project_name" up -d postgres temporal
postgres_id=$(docker compose --project-name "$project_name" ps -q postgres)
temporal_id=$(docker compose --project-name "$project_name" ps -q temporal)
postgres_ip=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$postgres_id")
temporal_ip=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$temporal_id")

DATABASE_URL="postgresql+asyncpg://salience:salience@${postgres_ip}:5432/salience" \
  .venv/bin/alembic upgrade head
TEST_DATABASE_URL="postgresql://salience:salience@${postgres_ip}:5432/salience" \
TEST_TEMPORAL_TARGET="${temporal_ip}:7233" \
  .venv/bin/pytest -p no:cacheprovider \
    tests/e2e/test_compose_worker_restart.py \
    tests/e2e/test_phase1_verifier.py \
    tests/e2e/test_phase2_verifier.py \
    tests/e2e/test_phase4_verifier.py \
    tests/e2e/test_phases_1_4_stack.py -q
echo "Retained inspection IDs are printed by the cross-phase report assertions."
