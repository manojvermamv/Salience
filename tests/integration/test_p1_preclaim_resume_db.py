"""Database boundaries for a locally proven preclaim publication resume."""

import os
from uuid import uuid4

import psycopg
import pytest

from test_p1_production_recovery_case_opening import (
    _restricted_role,
    opening_source,
)
from test_p1_production_policy_revocation import accounting, cycles, policy
from test_v4_migrations import _create_migration_database, _drop_migration_database, _migration


_FIND = "SELECT public.v4_find_preclaim_resume_v1(%s,%s,%s,%s,%s)"
_RECORD = "SELECT public.v4_record_preclaim_resume_v1(%s,%s,%s,%s,%s,%s,%s,%s)"


@pytest.fixture(scope="module")
def opening_database():
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, "p1_preclaim_sql_")
    try:
        result = _migration(database, "upgrade", "0056_p1_preclaim_permit_resume")
        assert result.returncode == 0, result.stdout + result.stderr
        yield database
    finally:
        _drop_migration_database(source, name)


def _lookup_arguments(case):
    request = case["request"]
    binding = request.publication_binding
    with psycopg.connect(case["database"]) as connection:
        permit_id = connection.execute(
            """SELECT current_permit_id FROM public.v4_production_current_dispatch
               WHERE operation_id=%s""",
            (request.operation_id,),
        ).fetchone()[0]
    return (
        request.operation_id, permit_id, request.approval_request_id,
        request.idempotency_key, binding.workflow_run_id,
    )


@pytest.mark.parametrize("opening_source", ["unclaimed"], indirect=True)
def test_preclaim_resume_requires_a_committed_exact_operator_receipt(opening_source):
    case, _, _ = opening_source
    args = _lookup_arguments(case)
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(_FIND, args).fetchone() == (None,)
        with pytest.raises(psycopg.errors.RaiseException, match="preclaim_resume"):
            connection.execute(_RECORD, (*args, uuid4(), uuid4(), None))


@pytest.mark.parametrize("opening_source", ["unclaimed"], indirect=True)
def test_preclaim_resume_functions_require_explicit_role_grants(opening_source):
    case, _, _ = opening_source
    args = _lookup_arguments(case)
    with _restricted_role(case["database"]) as role_db:
        with psycopg.connect(role_db) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(_FIND, args)
        with psycopg.connect(role_db) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(_RECORD, (*args, uuid4(), uuid4(), None))
