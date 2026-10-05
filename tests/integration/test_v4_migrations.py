import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest


def test_clean_upgrade_empty_rollback_and_populated_preservation():
    source = os.environ["TEST_DATABASE_URL"]
    name = "p0_migration_" + uuid4().hex
    parts = urlsplit(source)
    database = urlunsplit(parts._replace(path="/" + name))
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            def migrate(direction, target):
                return subprocess.run([sys.executable, "-m", "alembic", "-x", f"database_url={database}", direction, target], capture_output=True, text=True)

            assert migrate("upgrade", "head").returncode == 0
            assert migrate("downgrade", "0030_v4_schedule_cutover").returncode == 0
            assert migrate("upgrade", "head").returncode == 0
            assert migrate("downgrade", "0029_goal_create_receipts").returncode == 0
            assert migrate("upgrade", "head").returncode == 0
            assert migrate("downgrade", "0012_publication_profile_scope").returncode == 0
            assert migrate("upgrade", "head").returncode == 0
            assert migrate("downgrade", "0028_fixture_adapter_receipts").returncode == 0
            assert migrate("upgrade", "head").returncode == 0
            assert migrate("downgrade", "0028_fixture_adapter_receipts").returncode == 0
            workspace = uuid4()
            with psycopg.connect(database) as connection:
                expected_revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                connection.execute("INSERT INTO workspaces (id,slug,display_name) VALUES (%s,%s,'preservation')", (workspace, str(workspace)))
                connection.execute("INSERT INTO object_inventory (storage_key,workspace_id,content_hash,byte_size,content_type,retain_until) VALUES (%s,%s,%s,1,'text/plain',now())", (str(workspace) + "/key", workspace, "a" * 64))
            result = migrate("downgrade", "0012_publication_profile_scope")
            assert result.returncode != 0
            assert "preserve object identity" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM object_inventory").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_goals (id,workspace_id,state) VALUES (%s,%s,'active')", (uuid4(),workspace))
            result = migrate("downgrade", "0015_identity_lock")
            assert result.returncode != 0
            assert "preserve V4 cycle guards" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goals").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                subject=uuid4()
                goal=connection.execute("SELECT id FROM v4_goals").fetchone()[0]
                connection.execute("INSERT INTO identity_subjects (id,workspace_id,issuer,subject,expires_at) VALUES (%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(subject,workspace,str(subject)))
                connection.execute("INSERT INTO v4_goal_revisions (goal_id,revision,payload) VALUES (%s,1,'{}'),(%s,2,'{}')",(goal,goal))
                connection.execute("UPDATE v4_goals SET revision=2 WHERE id=%s",(goal,))
                connection.execute("INSERT INTO v4_goal_commands (goal_id,idempotency_key,subject_id,fingerprint,expected_revision,resulting_revision,reason) VALUES (%s,'fixture',%s,'fixture',1,2,'rollback preservation')",(goal,subject))
            result=migrate("downgrade","0018_cycle_outbox")
            assert result.returncode!=0
            assert "preserve goal revision command history" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_commands").fetchone()[0]==1
                assert connection.execute("SELECT revision FROM v4_goals WHERE id=%s",(goal,)).fetchone()[0]==2
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]==expected_revision
                connection.execute("INSERT INTO v4_goal_baselines (approval_id,goal_id,goal_revision,subject_id,bundle,expires_at,reason,traceparent) VALUES (%s,%s,2,%s,'{}',now()+interval '1 hour','preservation','fixture')",(uuid4(),goal,subject))
            result=migrate("downgrade","0019_goal_revision_commands")
            assert result.returncode!=0
            assert "preserve baseline approval and revocation history" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_baselines").fetchone()[0]==1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]==expected_revision
                connection.execute("INSERT INTO v4_goal_revisions (goal_id,revision,schema_version,payload) VALUES (%s,3,'GoalSpec.local.v2','{\"schema_version\":\"GoalSpec.local.v2\"}')", (goal,))
            result = migrate("downgrade", "0020_goal_baselines")
            assert result.returncode != 0 and "preserve V2 policy and context history" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_revisions WHERE revision=3").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_goal_revisions (goal_id,revision,schema_version,payload) VALUES (%s,4,'GoalSpec.local.v3','{\"schema_version\":\"GoalSpec.local.v3\"}')", (goal,))
            result = migrate("downgrade", "0022_program_policy_lock")
            assert result.returncode != 0 and "preserve V3" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_revisions WHERE revision=4").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_stop_scopes (workspace_id,scope_key,stopped) VALUES (%s,'workspace',true)", (workspace,))
            result = migrate("downgrade", "0025_accounting_categories")
            assert result.returncode != 0 and "preserve V4 stop, permit and case history" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT stopped FROM v4_stop_scopes WHERE workspace_id=%s", (workspace,)).fetchone()[0] is True
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_cycle_events (id,workspace_id,subject_id,goal_id,kind,traceparent,payload) VALUES (%s,%s,%s,%s,'outbox_dead_letter',%s,%s)", (uuid4(),workspace,subject,goal,"00-"+"a"*32+"-"+"b"*16+"-01",'{"message_id":"fixture"}'))
            result = migrate("downgrade", "0026_cycle_governance")
            assert result.returncode != 0 and "preserve V4 outbox dead-letter escalation history" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE kind='outbox_dead_letter'").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_cycle_events (id,workspace_id,subject_id,goal_id,kind,traceparent,payload) VALUES (%s,%s,%s,%s,'fixture_adapter_accepted',%s,%s)", (uuid4(),workspace,subject,goal,"00-"+"a"*32+"-"+"b"*16+"-01",'{"message_id":"fixture"}'))
            result = migrate("downgrade", "0027_auto_dispatch_escalation")
            assert result.returncode != 0 and "preserve V4 fixture adapter acceptance history" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE kind='fixture_adapter_accepted'").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
            assert migrate("upgrade", "head").returncode == 0
            with psycopg.connect(database) as connection:
                foreign_workspace, foreign_goal = uuid4(), uuid4()
                connection.execute("INSERT INTO workspaces (id,slug,display_name) VALUES (%s,%s,'foreign receipt fixture')", (foreign_workspace,str(foreign_workspace)))
                connection.execute("INSERT INTO v4_goals (id,workspace_id,state) VALUES (%s,%s,'active')", (foreign_goal,foreign_workspace))
                with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
                    connection.execute("INSERT INTO v4_goal_create_commands (workspace_id,subject_id,idempotency_key,fingerprint,goal_id) VALUES (%s,%s,'foreign-goal',%s,%s)", (workspace,subject,"a"*64,foreign_goal))
                connection.execute("INSERT INTO v4_goal_create_commands (workspace_id,subject_id,idempotency_key,fingerprint,goal_id) VALUES (%s,%s,'fixture-goal',%s,%s)", (workspace,subject,"a"*64,goal))
                with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
                    connection.execute("UPDATE v4_goal_create_commands SET fingerprint=%s WHERE goal_id=%s", ("b"*64,goal))
                final_revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            result = migrate("downgrade", "0028_fixture_adapter_receipts")
            assert result.returncode != 0 and "preserve exact V4 goal creation receipts" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_create_commands WHERE goal_id=%s", (goal,)).fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == final_revision
                legacy_schedule = uuid4()
                connection.execute("INSERT INTO job_schedules (id,workspace_id,name,schedule_expression,job_type) VALUES (%s,%s,%s,'every:60s','v4_fixture_legacy')", (legacy_schedule,workspace,str(legacy_schedule)))
                connection.execute("""
                    INSERT INTO v4_schedule_cutovers
                    (goal_id,workspace_id,goal_revision,legacy_schedule_id,actor_id,prepare_key,fingerprint,first_v4_slot,traceparent)
                    VALUES (%s,%s,2,%s,%s,'preserve',%s,now()+interval '1 hour',%s)
                """, (goal,workspace,legacy_schedule,subject,"a"*64,"00-"+"a"*32+"-"+"b"*16+"-01"))
                with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
                    connection.execute("UPDATE v4_schedule_cutovers SET fingerprint=%s WHERE goal_id=%s", ("b"*64,goal))
            result = migrate("downgrade", "0029_goal_create_receipts")
            assert result.returncode != 0 and "preserve V4 schedule cutover history" in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT state FROM v4_schedule_cutovers WHERE goal_id=%s", (goal,)).fetchone()[0] == "pending"
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == final_revision
        finally:
            admin.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))


@pytest.mark.parametrize("owner_mode", ["original", "foreign", "concurrent_foreign"])
def test_owner_binding_upgrade_preserves_or_holds_existing_history(owner_mode):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from datetime import datetime, timedelta, timezone
    from salience.cycles.admission import CycleAdmission
    from salience.cycles.contracts import GoalSpec
    from test_v4_cycle_admission import approved_goal, intent

    forged_owner = owner_mode != "original"
    source = os.environ["TEST_DATABASE_URL"]
    name = "item7_owner_" + uuid4().hex
    database = urlunsplit(urlsplit(source)._replace(path="/" + name))
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            def migrate(target):
                return subprocess.run([sys.executable,"-m","alembic","-x","database_url="+database,"upgrade",target],capture_output=True,text=True,timeout=30)

            assert migrate("0031_runtime_waits").returncode == 0
            workspace, subject, other = uuid4(), uuid4(), uuid4()
            with psycopg.connect(database) as connection:
                connection.execute("INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'owner upgrade fixture')",(workspace,str(workspace)))
                for identity in [subject, other]:
                    connection.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(identity,workspace,str(identity)))
                for scope in ["goals:write","goals:approve","cycles:write"]:
                    connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",(workspace,str(subject),scope))
            service = CycleAdmission(database,workspace_id=workspace,subject_id=subject)
            spec = GoalSpec(objective="Preserve hold ownership",metric_versions=("fixture-quality@1",),audience="internal",account_refs=("fixture-account",),brand_scope="fixture-brand",source_policy="fixture-only",horizon_end=datetime.now(timezone.utc)+timedelta(days=1))
            goal = approved_goal(service,spec)
            cycle = service.admit(intent(service,goal))["cycle_id"]
            with psycopg.connect(database) as connection:
                context, operation = connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s",(cycle,)).fetchone()
                connection.execute("""INSERT INTO v4_runtime_holds(cycle_id,context_id,operation_id,workspace_id,owner_id,reason)
                    VALUES(%s,%s,%s,%s,%s,'held_timeout')""",(cycle,context,operation,workspace,other if forged_owner else subject))
                # Revocation cannot rewrite a correctly recorded original owner.
                connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s",(subject,))
                before = connection.execute("SELECT to_jsonb(h) FROM v4_runtime_holds h WHERE cycle_id=%s",(cycle,)).fetchone()[0]
                if owner_mode == "concurrent_foreign":
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(migrate,"head")
                        try:
                            with psycopg.connect(database,autocommit=True) as observer:
                                until = time.monotonic()+10
                                while time.monotonic() < until:
                                    blocked = observer.execute("""SELECT 1 FROM pg_stat_activity
                                        WHERE datname=current_database() AND pid<>pg_backend_pid()
                                        AND wait_event_type='Lock' AND query LIKE '%%v4_runtime_holds%%'""").fetchone()
                                    if blocked:
                                        break
                                    time.sleep(.02)
                                assert blocked, "upgrade must wait on the concurrent writer"
                        finally:
                            # Commit while the upgrade is waiting for its table
                            # lock; the preflight must see this exact new row.
                            connection.commit()
                        upgraded = future.result(timeout=30)
                else:
                    connection.commit()
                    upgraded = migrate("head")
            if forged_owner:
                assert upgraded.returncode != 0 and "preserve and inspect history" in upgraded.stderr
            else:
                assert upgraded.returncode == 0, upgraded.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT to_jsonb(h) FROM v4_runtime_holds h WHERE cycle_id=%s",(cycle,)).fetchone()[0] == before
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == ("0031_runtime_waits" if forged_owner else "0035_parallel_agent_teams")
        finally:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",(name,))
            admin.execute(psycopg.sql.SQL("DROP DATABASE {}").format(psycopg.sql.Identifier(name)))
