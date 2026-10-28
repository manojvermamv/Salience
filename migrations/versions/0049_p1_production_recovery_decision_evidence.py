"""Retain split stop and decision evidence in production recovery events."""

from alembic import op


revision = "0049_p1_production_recovery_decision_evidence"
down_revision = "0048_p1_production_schedule_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """DO $$
        DECLARE
            definition text;
        BEGIN
            definition := pg_get_functiondef(
                'public.v4_validate_production_recovery_event_v1()'::regprocedure
            );
            definition := replace(
                definition,
                'v4_validate_production_recovery_event_v1',
                'v4_validate_production_recovery_event_v2'
            );
            definition := replace(
                definition,
                '''policy_revision'',''stop_revision'',''route_id'',''slot_id'',',
                '''policy_revision'',''stop_revision'',''route_id'',''slot_id'','
                '''stage_decision_id'',''policy_decision_id'','
                '''workspace_stop_revision'',''goal_stop_revision'','
                '''route_decision_id'',''command_id'', '
            );
            definition := replace(
                definition,
                '            IF NEW.payload_sha256 IS DISTINCT FROM',
                '            IF NEW.event_kind = ''same_operation_no_effect_reissue'''
                || ' AND ('
                || 'NOT (NEW.payload ? ''stage_decision_id'')'
                || ' OR jsonb_typeof(NEW.payload->''stage_decision_id'') IS DISTINCT FROM ''string'''
                || ' OR NEW.payload->>''stage_decision_id'' !~ '
                || '''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'''
                || ' OR NOT (NEW.payload ? ''policy_decision_id'')'
                || ' OR jsonb_typeof(NEW.payload->''policy_decision_id'') IS DISTINCT FROM ''string'''
                || ' OR NEW.payload->>''policy_decision_id'' !~ '
                || '''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'''
                || ' OR NEW.payload->>''policy_decision_id'' IS DISTINCT FROM NEW.policy_decision_id::text'
                || ' OR NOT (NEW.payload ? ''workspace_stop_revision'')'
                || ' OR jsonb_typeof(NEW.payload->''workspace_stop_revision'') IS DISTINCT FROM ''number'''
                || ' OR NEW.payload->>''workspace_stop_revision'' !~ ''^[1-9][0-9]*$'''
                || ' OR NOT (NEW.payload ? ''goal_stop_revision'')'
                || ' OR jsonb_typeof(NEW.payload->''goal_stop_revision'') IS DISTINCT FROM ''number'''
                || ' OR NEW.payload->>''goal_stop_revision'' !~ ''^[1-9][0-9]*$'''
                || ' OR NOT (NEW.payload ? ''route_decision_id'')'
                || ' OR jsonb_typeof(NEW.payload->''route_decision_id'') IS DISTINCT FROM ''string'''
                || ' OR NEW.payload->>''route_decision_id'' !~ '
                || '''^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'''
                || ' OR NOT (NEW.payload ? ''event_key'')'
                || ' OR jsonb_typeof(NEW.payload->''event_key'') IS DISTINCT FROM ''string'''
                || ' OR NEW.payload->>''event_key'' IS DISTINCT FROM NEW.event_key) THEN'
                || ' RAISE EXCEPTION ''same-operation no-effect event requires exact decision and split stop evidence'';'
                || ' END IF;'
                || ' IF NEW.payload_sha256 IS DISTINCT FROM'
            );
            IF definition = pg_get_functiondef(
                'public.v4_validate_production_recovery_event_v1()'::regprocedure
            ) OR position('workspace_stop_revision' IN definition) = 0
                 OR position('same-operation no-effect event requires exact decision' IN definition) = 0 THEN
                RAISE EXCEPTION 'failed to extend production recovery event validator';
            END IF;
            EXECUTE definition;
            DROP TRIGGER validate_v4_production_recovery_event_v1
                ON public.v4_production_recovery_events;
            CREATE TRIGGER validate_v4_production_recovery_event_v2
                BEFORE INSERT ON public.v4_production_recovery_events
                FOR EACH ROW
                EXECUTE FUNCTION public.v4_validate_production_recovery_event_v2();
            REVOKE ALL ON FUNCTION
                public.v4_validate_production_recovery_event_v2() FROM PUBLIC;
        END
        $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_validate_production_recovery_deadline_evidence_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NEW.event_kind = 'escalation' AND (
                   jsonb_typeof(NEW.payload->'schema_version') IS DISTINCT FROM 'string'
                   OR NEW.payload->>'schema_version' IS DISTINCT FROM
                      'salience.p1-production-recovery-deadline.v1'
                   OR jsonb_typeof(NEW.payload->'status') IS DISTINCT FROM 'string'
                   OR length(NEW.payload->>'status') = 0
                   OR NOT (NEW.payload ? 'policy_decision_id')
                   OR jsonb_typeof(NEW.payload->'policy_decision_id') IS DISTINCT FROM 'string'
                   OR NEW.payload->>'policy_decision_id' !~
                      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                   OR NEW.payload->>'policy_decision_id' IS DISTINCT FROM NEW.policy_decision_id::text
                   OR NOT (NEW.payload ? 'route_decision_id')
                   OR jsonb_typeof(NEW.payload->'route_decision_id') IS DISTINCT FROM 'string'
                   OR NEW.payload->>'route_decision_id' !~
                      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                   OR NOT (NEW.payload ? 'event_key')
                   OR jsonb_typeof(NEW.payload->'event_key') IS DISTINCT FROM 'string'
                   OR NEW.payload->>'event_key' IS DISTINCT FROM NEW.event_key
                   OR (NEW.payload->>'status' = 'deadline_suspend'
                       AND NOT (NEW.payload ? 'command_id'))
                   OR (NEW.payload ? 'command_id'
                       AND (jsonb_typeof(NEW.payload->'command_id') IS DISTINCT FROM 'string'
                            OR NEW.payload->>'command_id' !~
                              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'))
               ) THEN
                RAISE EXCEPTION
                    'deadline event requires its exact policy, route, and typed command evidence';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER validate_v4_production_recovery_deadline_evidence_v1
           BEFORE INSERT ON public.v4_production_recovery_events
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_validate_production_recovery_deadline_evidence_v1()"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_validate_production_recovery_deadline_evidence_v1() FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        """LOCK TABLE public.v4_production_recovery_events
           IN ACCESS EXCLUSIVE MODE"""
    )
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.v4_production_recovery_events
                WHERE payload ?| ARRAY[
                    'stage_decision_id','policy_decision_id',
                    'workspace_stop_revision','goal_stop_revision',
                    'route_decision_id','command_id'
                ]
            ) THEN
                RAISE EXCEPTION
                    'preserve production recovery decision and typed command evidence';
            END IF;
        END
        $$"""
    )
    op.execute(
        """DROP TRIGGER IF EXISTS validate_v4_production_recovery_deadline_evidence_v1
           ON public.v4_production_recovery_events"""
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.v4_validate_production_recovery_deadline_evidence_v1()"
    )
    op.execute(
        """DROP TRIGGER validate_v4_production_recovery_event_v2
           ON public.v4_production_recovery_events"""
    )
    op.execute(
        """CREATE TRIGGER validate_v4_production_recovery_event_v1
           BEFORE INSERT ON public.v4_production_recovery_events
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_validate_production_recovery_event_v1()"""
    )
    op.execute(
        "DROP FUNCTION public.v4_validate_production_recovery_event_v2()"
    )
