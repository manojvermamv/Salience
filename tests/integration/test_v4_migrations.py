import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg


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
            assert migrate("downgrade", "0012_publication_profile_scope").returncode == 0
            assert migrate("upgrade", "head").returncode == 0
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
        finally:
            admin.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))
