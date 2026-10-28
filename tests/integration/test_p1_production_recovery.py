"""Task 6 enrollment-to-recovery handoff against the immutable 0047 schema."""

from importlib import import_module

import psycopg


def test_task6_enrollment_is_the_exact_pre_admission_recovery_handoff():
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    migration_tests = import_module("test_v4_migrations")
    module = import_module("salience.cycles.production_schedule_control")

    with schedule_tests._production_enrollment_fixture("task6_recovery_handoff_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        writer = module.ProductionIntentEnrollmentWriter(control, binding)
        result = writer.enroll_and_admit(key)
        assert result["admission"]["disposition"] == "review_required"

        recovery_owner = {
            "workspace_id": binding.workspace_id,
            "subject_id": binding.source_owner_subject_id,
            "cycle": {"goal_id": binding.goal_id, "intent_id": key.intent_id},
        }
        with psycopg.connect(database) as connection:
            intent = connection.execute(
                """SELECT due_at,expires_at,fingerprint,eligibility_revision
                   FROM v4_cycle_intents WHERE id=%s""",
                (key.intent_id,),
            ).fetchone()
            enrollment = connection.execute(
                """SELECT intent_id,eligibility_revision,workspace_id,goal_id,
                          source_owner_id,route_fence_decision_id
                   FROM v4_production_intent_enrollments
                   WHERE intent_id=%s AND eligibility_revision=%s""",
                (key.intent_id, key.eligibility_revision),
            ).fetchone()
            admission = connection.execute(
                """SELECT disposition,cycle_id FROM v4_admissions
                   WHERE intent_id=%s AND eligibility_revision=%s""",
                (key.intent_id, key.eligibility_revision),
            ).fetchone()
            assert enrollment is not None
            assert admission == ("review_required", None)
            assert enrollment[4] == binding.source_owner_subject_id

            handoff = migration_tests._insert_0047_recovery_work(
                connection,
                recovery_owner,
                source_discriminant="pre_admission_intent_case",
                source_id=key.intent_id,
                source_revision=key.eligibility_revision,
                eligibility_revision=key.eligibility_revision,
                intent_id=key.intent_id,
                reason="admission_review_required",
                state="unadmitted",
                deadline=intent[1],
                due_at=intent[0],
                expires_at=intent[1],
                request_fingerprint=intent[2],
            )
            assert handoff["source_id"] == enrollment[0] == key.intent_id
            assert handoff["source_revision"] == enrollment[1]
            assert handoff["state"] == "unadmitted"
            assert handoff["owner_id"] == enrollment[4]
            assert handoff["source_fingerprint"]
            assert handoff["cycle_id"] is None
            assert handoff["operation_id"] is None
            assert handoff["snapshot"] is None

            joined = connection.execute(
                """SELECT work.source_id,work.source_revision,
                          enrollment.route_fence_decision_id,
                          admission.disposition
                   FROM v4_production_recovery_work work
                   JOIN v4_production_intent_enrollments enrollment
                     ON enrollment.intent_id=work.intent_id
                    AND enrollment.eligibility_revision=work.eligibility_revision
                    AND enrollment.workspace_id=work.workspace_id
                    AND enrollment.goal_id=work.goal_id
                   JOIN v4_admissions admission
                     ON admission.intent_id=work.intent_id
                    AND admission.eligibility_revision=work.eligibility_revision
                   WHERE work.work_id=%s""",
                (handoff["work_id"],),
            ).fetchone()
            assert joined == (
                key.intent_id,
                key.eligibility_revision,
                enrollment[5],
                "review_required",
            )
