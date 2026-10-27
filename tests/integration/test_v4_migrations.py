import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb


_MIGRATION_ROOT = Path(__file__).parents[2]
_TASK5_EVIDENCE = (
    _MIGRATION_ROOT
    / "artifacts/v4-next-p1/agents/p1-publication-recovery-0043/"
    / "task5-installed-image-migration.json"
)
_REVISION_0041 = "0041_native_publication_sources"
_REVISION_0042 = "0042_publication_recovery_holds"
_REVISION_0043 = "0043_complete_publication_recovery_liabilities"
_REVISION_0044 = "0044_production_policy_revocation"
_HOLD_TABLE = "public.v4_legacy_publication_recovery_holds"
_V1_KEYS = (
    "dispatch",
    "context",
    "cycle",
    "job",
    "permit_claim",
    "permit",
    "fixture_receipt",
    "fixture_progress",
    "publication_request",
    "publication_plan",
    "publication_attempt",
    "external_effect",
    "budget_reservation",
    "output",
    "remote_publication_receipts",
    "publication_status_events",
    "cycle_events",
    "cycle_outbox",
    "job_checkpoints",
    "audit_events",
    "provenance_records",
)


def _run_cleanup_actions(actions):
    """Run every cleanup action and report failures except exact absent-resource cases."""
    outcomes = []
    failures = []
    absent_errors = {
        "container stop": re.compile(
            r"^Error response from daemon: No such container: [A-Za-z0-9_.-]+$"
        ),
        "network removal": re.compile(
            r"^Error response from daemon: network [A-Za-z0-9_.-]+ not found\nexit status 1$"
        ),
        "worker image removal": re.compile(
            r"^Error response from daemon: No such image: \S+$"
        ),
    }

    for name, action in actions:
        try:
            result = action()
        except FileNotFoundError as exc:
            if name == "staged wheel removal":
                outcomes.append({"name": name, "status": "already_absent"})
                continue
            error = f"{name}: {exc}"
            outcomes.append({"name": name, "status": "failed", "error": str(exc)})
            failures.append(error)
            continue
        except Exception as exc:
            error = f"{name}: {exc}"
            outcomes.append({"name": name, "status": "failed", "error": str(exc)})
            failures.append(error)
            continue

        if not isinstance(result, subprocess.CompletedProcess):
            outcomes.append({"name": name, "status": "success"})
            continue

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        outcome = {
            "name": name,
            "status": "success" if result.returncode == 0 else "failed",
            "argv": list(result.args) if isinstance(result.args, (list, tuple)) else result.args,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        if result.returncode != 0:
            message = stderr.strip() or stdout.strip()
            absent_pattern = absent_errors.get(name)
            if (
                result.returncode == 1
                and absent_pattern is not None
                and absent_pattern.fullmatch(message)
            ):
                outcome["status"] = "already_absent"
            else:
                failures.append(f"{name}: exit {result.returncode}: {message or 'no error output'}")
        outcomes.append(outcome)

    return outcomes, failures


@contextmanager
def _cleanup_scope(initial_actions, state):
    actions = list(initial_actions)
    summary = {}
    try:
        yield actions, summary
    finally:
        outcomes, failures = _run_cleanup_actions(reversed(actions))
        summary["actions"] = outcomes
        summary["failures"] = failures
        if failures:
            raise AssertionError("Task5 cleanup failures: " + "; ".join(failures))
        state["cleaned"] = True


def _remove_staged_wheel(stage):
    shutil.rmtree(stage)


def _atomic_write_task5_evidence(evidence):
    _TASK5_EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _TASK5_EVIDENCE.with_name(
        f".{_TASK5_EVIDENCE.name}.{uuid4().hex}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(evidence, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, _TASK5_EVIDENCE)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_task5_source_snapshot(source_root, recorded_source_commit=None):
    head_command = ["git", "rev-parse", "HEAD"]
    head = subprocess.run(
        head_command,
        cwd=source_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert head.returncode == 0, head.stdout + head.stderr
    status_command = ["git", "status", "--porcelain", "--untracked-files=all"]
    status = subprocess.run(
        status_command,
        cwd=source_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert status.returncode == 0, status.stdout + status.stderr
    source_commit = head.stdout.strip()
    if recorded_source_commit is not None:
        assert source_commit == recorded_source_commit, (
            f"HEAD {source_commit} does not match recorded source commit "
            f"{recorded_source_commit}"
        )
    assert not status.stdout.strip(), (
        "Task5 source tree must be clean before build; staged, unstaged, or "
        "non-ignored untracked paths remain: "
        + status.stdout.strip()
    )
    return {
        "source_commit": source_commit,
        "recorded_source_commit": recorded_source_commit or source_commit,
        "head_check": {
            "command_argv": head_command,
            "exit_code": head.returncode,
            "stdout": head.stdout,
            "stderr": head.stderr,
        },
        "clean_status_check": {
            "command_argv": status_command,
            "exit_code": status.returncode,
            "stdout": status.stdout,
            "stderr": status.stderr,
        },
    }


def _installed_wheel_digest(image_id):
    retained_wheel = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            image_id,
            "python",
            "-c",
            "from hashlib import sha256; print(sha256(open('/opt/salience-artifacts/salience.whl', 'rb').read()).hexdigest())",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert retained_wheel.returncode == 0, retained_wheel.stdout + retained_wheel.stderr
    return retained_wheel.stdout.strip()


@pytest.fixture(scope="module")
def task5_worker_image(tmp_path_factory):
    """Build the real wheel-only worker image from a temporary retained wheel."""
    _TASK5_EVIDENCE.unlink(missing_ok=True)
    before_wheel_build = _verify_task5_source_snapshot(_MIGRATION_ROOT)
    source_commit = before_wheel_build["source_commit"]
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the installed-image migration contract")
    docker_info = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if docker_info.returncode != 0:
        pytest.skip(f"Docker daemon is unavailable: {docker_info.stderr.strip()}")

    build_dir = tmp_path_factory.mktemp("task5-wheel-build")
    wheel_dir = build_dir / "wheel"
    wheel_dir.mkdir()
    wheel_build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(_MIGRATION_ROOT),
        ],
        cwd=_MIGRATION_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert wheel_build.returncode == 0, wheel_build.stdout + wheel_build.stderr
    wheels = list(wheel_dir.glob("salience-*.whl"))
    assert len(wheels) == 1, f"expected one retained Salience wheel, got {wheels}"

    staged_wheel_root = _MIGRATION_ROOT / "build"
    staged_wheel_root.mkdir(parents=True, exist_ok=True)
    stage = staged_wheel_root / f".task5-wheel-{uuid4().hex}"
    stage.mkdir()
    shutil.copy2(wheels[0], stage / "salience.whl")
    project = "salience-task5-" + uuid4().hex[:12]
    image_ref = f"{project}-worker:latest"
    compose = ["docker", "compose", "-p", project, "--profile", "application"]
    environment = os.environ.copy()
    environment["SALIENCE_WHEEL_FILE"] = (stage / "salience.whl").relative_to(
        _MIGRATION_ROOT
    ).as_posix()
    cleanup_state = {"cleaned": False}
    # Actions are stored in reverse execution order for _cleanup_scope.
    cleanup_actions = [
        ("staged wheel removal", lambda: _remove_staged_wheel(stage)),
        (
            "worker image removal",
            lambda: subprocess.run(
                ["docker", "image", "rm", "--force", image_ref],
                capture_output=True,
                text=True,
                timeout=30,
            ),
        ),
        (
            "Compose project teardown",
            lambda: subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans"],
                cwd=_MIGRATION_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            ),
        ),
    ]
    try:
        before_image_build = _verify_task5_source_snapshot(
            _MIGRATION_ROOT, recorded_source_commit=source_commit
        )
        image_build = subprocess.run(
            [*compose, "build", "worker"],
            cwd=_MIGRATION_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert image_build.returncode == 0, image_build.stdout + image_build.stderr
        image_result = subprocess.run(
            ["docker", "image", "inspect", image_ref],
            cwd=_MIGRATION_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert image_result.returncode == 0, image_result.stdout + image_result.stderr
        image_metadata = json.loads(image_result.stdout)[0]
        image_id = image_metadata["Id"]
        assert image_id.startswith("sha256:"), f"expected worker image ID, got {image_id!r}"
        yield {
            "source_commit": source_commit,
            "source_provenance": {
                "before_wheel_build": before_wheel_build,
                "before_image_build": before_image_build,
            },
            "image_id": image_id,
            "image_digest": image_id,
            "image_repo_digests": image_metadata.get("RepoDigests") or [],
            "wheel": wheels[0],
            "retained_wheel_sha256": hashlib.sha256(
                (stage / "salience.whl").read_bytes()
            ).hexdigest(),
            "cleanup_actions": cleanup_actions,
            "cleanup_state": cleanup_state,
        }
    finally:
        if not cleanup_state["cleaned"]:
            outcomes, failures = _run_cleanup_actions(reversed(cleanup_actions))
            if failures:
                raise AssertionError("Task5 image cleanup failures: " + "; ".join(failures))
            cleanup_state["cleaned"] = True


def test_installed_alembic_config_resolves_inside_runtime_root(task5_worker_image):
    import yaml

    image_id = task5_worker_image["image_id"]
    runtime_config = "/usr/local/lib/python3.13/site-packages/alembic.ini"
    wheel_digest = hashlib.sha256(task5_worker_image["wheel"].read_bytes()).hexdigest()
    in_image_digest = _installed_wheel_digest(image_id)
    assert in_image_digest == wheel_digest

    resolved_heads = subprocess.run(
        ["docker", "run", "--rm", image_id, "alembic", "-c", runtime_config, "heads"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resolved_heads.returncode == 0, resolved_heads.stdout + resolved_heads.stderr
    assert _REVISION_0043 in resolved_heads.stdout
    assert "(head)" in resolved_heads.stdout

    compose = yaml.safe_load((_MIGRATION_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    assert compose["services"]["migrate"]["command"] == [
        "alembic",
        "-c",
        runtime_config,
        "upgrade",
        "head",
    ]


def test_installed_image_migrates_disposable_database_to_0043(task5_worker_image):
    import yaml

    compose = yaml.safe_load((_MIGRATION_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    postgres_image = compose["services"]["postgres"]["image"]
    network = "salience-task5-db-" + uuid4().hex[:12]
    container = "salience-task5-postgres-" + uuid4().hex[:12]
    test_password = "task5_" + uuid4().hex
    database_name = None
    source = None
    runtime_role = None
    evidence = {}
    with _cleanup_scope(
        task5_worker_image["cleanup_actions"], task5_worker_image["cleanup_state"]
    ) as (cleanup_actions, cleanup_summary):
        created_network = subprocess.run(
            ["docker", "network", "create", network],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert created_network.returncode == 0, created_network.stdout + created_network.stderr
        cleanup_actions.append(
            (
                "network removal",
                lambda: subprocess.run(
                    ["docker", "network", "rm", network],
                    capture_output=True,
                    text=True,
                    timeout=30,
                ),
            )
        )
        network_info = subprocess.run(
            ["docker", "network", "inspect", network],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert network_info.returncode == 0, network_info.stdout + network_info.stderr
        network_id = json.loads(network_info.stdout)[0]["Id"]

        postgres_inspect = subprocess.run(
            ["docker", "image", "inspect", postgres_image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert postgres_inspect.returncode == 0, (
            postgres_inspect.stdout + postgres_inspect.stderr
        )
        postgres_metadata = json.loads(postgres_inspect.stdout)[0]

        started = subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                container,
                "--network",
                network,
                "--network-alias",
                "task5-postgres",
                "--publish",
                "127.0.0.1::5432",
                "--env",
                "POSTGRES_DB=postgres",
                "--env",
                "POSTGRES_USER=salience",
                "--env",
                f"POSTGRES_PASSWORD={test_password}",
                postgres_image,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert started.returncode == 0, started.stdout + started.stderr
        cleanup_actions.append(
            (
                "container stop",
                lambda: subprocess.run(
                    ["docker", "stop", container],
                    capture_output=True,
                    text=True,
                    timeout=60,
                ),
            )
        )

        ready_deadline = time.monotonic() + 90
        while True:
            ready = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "salience", "-d", "postgres"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() >= ready_deadline:
                raise AssertionError(f"disposable PostgreSQL did not become ready: {ready.stderr}")
            time.sleep(0.25)

        published_port = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert published_port.returncode == 0, published_port.stdout + published_port.stderr
        port = published_port.stdout.strip().rsplit(":", 1)[-1]
        source = f"postgresql://salience:{test_password}@127.0.0.1:{port}/postgres"

        connection_deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(source, connect_timeout=2) as connection:
                    postgres_version = connection.execute("SELECT version()").fetchone()[0]
                    roles_before = [
                        row[0]
                        for row in connection.execute(
                            "SELECT rolname FROM pg_roles ORDER BY rolname"
                        ).fetchall()
                    ]
                break
            except psycopg.OperationalError:
                if time.monotonic() >= connection_deadline:
                    raise
                time.sleep(0.25)

        database_name, database = _create_migration_database(source, "task5_disposable_")
        cleanup_actions.append(
            (
                "disposable database and fixture role removal",
                lambda source=source, database_name=database_name: _drop_migration_database(
                    source, database_name, runtime_role
                ),
            )
        )
        image_database_url = (
            "postgresql+asyncpg://salience:"
            f"{test_password}@task5-postgres:5432/{database_name}"
        )
        command_argv = [
            "alembic",
            "-c",
            "/usr/local/lib/python3.13/site-packages/alembic.ini",
            "upgrade",
            "head",
        ]
        in_image_wheel_sha256 = _installed_wheel_digest(task5_worker_image["image_id"])
        assert in_image_wheel_sha256 == task5_worker_image["retained_wheel_sha256"]

        migrated = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--env",
                f"DATABASE_URL={image_database_url}",
                task5_worker_image["image_id"],
                *command_argv,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr

        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchall() == [(_REVISION_0043,)]
            assert _alembic_version_num_type(connection) == ("character varying", 64)
            roles_after = [
                row[0]
                for row in connection.execute(
                    "SELECT rolname FROM pg_roles ORDER BY rolname"
                ).fetchall()
            ]
            assert roles_after == roles_before

            nonempty_tables = []
            for (table_name,) in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename <> 'alembic_version' ORDER BY tablename"
            ).fetchall():
                row_count = connection.execute(
                    psycopg.sql.SQL("SELECT count(*) FROM {}").format(
                        psycopg.sql.Identifier("public", table_name)
                    )
                ).fetchone()[0]
                if row_count:
                    nonempty_tables.append((table_name, row_count))
            assert nonempty_tables == []

        runtime_role = _fixture_role(database)
        with psycopg.connect(database) as connection:
            contract = _hold_contract(connection, runtime_role)
            assert contract["public_privileges"] == 0
            assert contract["role"] == (
                False,
                True,
                False,
                True,
                True,
                False,
                False,
                False,
                False,
                False,
            )
            _assert_0043_schema_and_guard(connection, runtime_role)
        evidence = {
            "schema": "salience.task5-installed-image-migration",
            "schema_version": 1,
            "evidence_scope": "Task5 installed-image migration run qualification only",
            "task8_candidate_lock_evidence": False,
            "source_commit": task5_worker_image["source_commit"],
            "source_provenance": task5_worker_image["source_provenance"],
            "worker_image": {
                "id": task5_worker_image["image_id"],
                "digest": task5_worker_image["image_digest"],
                "digest_basis": "Docker content-addressed image ID",
                "repo_digests": task5_worker_image["image_repo_digests"],
            },
            "wheel": {
                "retained_sha256": task5_worker_image["retained_wheel_sha256"],
                "in_image_path": "/opt/salience-artifacts/salience.whl",
                "in_image_sha256": in_image_wheel_sha256,
            },
            "migration": {
                "command_argv": command_argv,
                "stdout": migrated.stdout,
                "stderr": migrated.stderr,
                "exit_code": migrated.returncode,
                "final_revision": _REVISION_0043,
                "database_url_environment_variable": "DATABASE_URL",
            },
            "disposable_postgres": {
                "image_reference": postgres_image,
                "image_id": postgres_metadata["Id"],
                "image_repo_digests": postgres_metadata.get("RepoDigests") or [],
                "server_version": postgres_version,
                "network_name": network,
                "network_id": network_id,
                "container_name": container,
                "database_name": database_name,
                "boundary": (
                    "newly-created database in a per-run PostgreSQL container on an "
                    "isolated per-run Docker network; loopback-only published port"
                ),
                "disposable": True,
            },
            "safety": {
                "production_database_used": False,
                "production_credentials_used": False,
                "worker_started": False,
                "provider_effects_performed": False,
                "external_effects_performed": False,
                "effects_enabled": False,
            },
            "validity": {
                "invalidated_by": [
                    "a later source commit",
                    "a later worker image build or image ID/digest change",
                ]
            },
        }

    evidence["generated_at_utc"] = (
        datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    evidence["cleanup"] = cleanup_summary["actions"]
    assert cleanup_summary["failures"] == []
    _atomic_write_task5_evidence(evidence)


def test_cleanup_aggregates_failures_and_continues_after_each_action():
    from subprocess import CompletedProcess

    called = []

    def failed_action():
        called.append("network removal")
        return CompletedProcess(["docker", "network", "rm", "broken"], 2, "", "daemon error")

    def successful_action():
        called.append("container stop")
        return CompletedProcess(["docker", "stop", "container"], 0, "container\n", "")

    results, failures = _run_cleanup_actions(
        [("network removal", failed_action), ("container stop", successful_action)]
    )

    assert called == ["network removal", "container stop"]
    assert [result["status"] for result in results] == ["failed", "success"]
    assert failures == ["network removal: exit 2: daemon error"]


@pytest.mark.parametrize(
    ("name", "returncode", "stderr"),
    [
        (
            "container stop",
            1,
            "Error response from daemon: No such container: task5-container\n",
        ),
        (
            "network removal",
            1,
            "Error response from daemon: network task5-network not found\nexit status 1\n",
        ),
        (
            "worker image removal",
            1,
            "Error response from daemon: No such image: salience-task5-worker:latest\n",
        ),
    ],
)
def test_cleanup_tolerates_only_documented_absent_container_or_network(
    name, returncode, stderr
):
    from subprocess import CompletedProcess

    result = CompletedProcess(["docker", "cleanup"], returncode, "", stderr)
    outcomes, failures = _run_cleanup_actions([(name, lambda: result)])

    assert outcomes[0]["status"] == "already_absent"
    assert failures == []


def test_cleanup_rejects_undocumented_absence_errors():
    from subprocess import CompletedProcess

    result = CompletedProcess(
        ["docker", "stop", "task5-container"],
        1,
        "",
        "Error response from daemon: container is still in use",
    )
    outcomes, failures = _run_cleanup_actions([("container stop", lambda: result)])

    assert outcomes[0]["status"] == "failed"
    assert failures == [
        "container stop: exit 1: Error response from daemon: container is still in use"
    ]


def _synthetic_task5_source_repo(root):
    root.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Task5 Test"], cwd=root, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "task5-test@example.invalid"],
        cwd=root,
        check=True,
    )
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools']\nbuild-backend = 'setuptools.build_meta'\n"
        "[project]\nname = 'synthetic-task5-wheel'\nversion = '0.0.1'\n"
        "[tool.setuptools.packages.find]\nwhere = ['src']\n",
        encoding="utf-8",
    )
    package = root / "src" / "synthetic_package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("BASELINE = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "synthetic source baseline"],
        cwd=root,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_task5_source_gate_rejects_untracked_package_source_before_build_or_evidence(
    tmp_path,
):
    repo = tmp_path / "synthetic-repo"
    source_commit = _synthetic_task5_source_repo(repo)
    untracked_module = repo / "src" / "synthetic_package" / "would_ship_in_wheel.py"
    # Setuptools package discovery includes Python modules under src packages.
    untracked_module.write_text("UNTRACKED_WHEEL_CONTENT = True\n", encoding="utf-8")
    wheel_build_attempts = []
    evidence_path = tmp_path / "task5-evidence.json"

    def qualification():
        _verify_task5_source_snapshot(repo, source_commit)
        wheel_build_attempts.append("pip wheel")
        evidence_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="clean"):
        qualification()

    assert untracked_module.exists()
    assert wheel_build_attempts == []
    assert not evidence_path.exists()


def test_task5_source_snapshot_records_exact_clean_status_command(tmp_path):
    repo = tmp_path / "synthetic-repo"
    source_commit = _synthetic_task5_source_repo(repo)

    snapshot = _verify_task5_source_snapshot(repo, source_commit)

    assert snapshot["source_commit"] == source_commit
    assert snapshot["recorded_source_commit"] == source_commit
    assert snapshot["head_check"]["command_argv"] == ["git", "rev-parse", "HEAD"]
    assert snapshot["head_check"]["stdout"] == source_commit + "\n"
    assert snapshot["clean_status_check"] == {
        "command_argv": [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
    }


@pytest.mark.parametrize("change_kind", ["unstaged", "staged"])
def test_task5_source_gate_rejects_tracked_changes(tmp_path, change_kind):
    repo = tmp_path / "synthetic-repo"
    source_commit = _synthetic_task5_source_repo(repo)
    package_source = repo / "src" / "synthetic_package" / "__init__.py"
    package_source.write_text("BASELINE = False\n", encoding="utf-8")
    if change_kind == "staged":
        subprocess.run(["git", "add", str(package_source)], cwd=repo, check=True)

    with pytest.raises(AssertionError, match="clean"):
        _verify_task5_source_snapshot(repo, source_commit)


def test_task5_source_gate_rejects_head_mismatch(tmp_path):
    repo = tmp_path / "synthetic-repo"
    original_commit = _synthetic_task5_source_repo(repo)
    package_source = repo / "src" / "synthetic_package" / "__init__.py"
    package_source.write_text("BASELINE = False\n", encoding="utf-8")
    subprocess.run(["git", "add", str(package_source)], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "advance source commit"],
        cwd=repo,
        check=True,
    )

    with pytest.raises(AssertionError, match="recorded source commit"):
        _verify_task5_source_snapshot(repo, original_commit)


def _create_migration_database(source, prefix):
    name = prefix + uuid4().hex[:12]
    database = urlunsplit(urlsplit(source)._replace(path="/" + name))
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name))
        )
    return name, database


def _drop_migration_database(source, name, role=None):
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",
            (name,),
        )
        admin.execute(
            psycopg.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                psycopg.sql.Identifier(name)
            )
        )
        if role:
            admin.execute(
                psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(
                    psycopg.sql.Identifier(role)
                )
            )


def _migration(database, direction, target):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            f"database_url={database}",
            direction,
            target,
        ],
        cwd=_MIGRATION_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _fixture_role(database):
    role = "fixture_runtime_" + uuid4().hex[:12]
    with psycopg.connect(database) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE ROLE {} NOLOGIN").format(
                psycopg.sql.Identifier(role)
            )
        )
        connection.execute(
            psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                psycopg.sql.Identifier(role)
            )
        )
        connection.execute(
            psycopg.sql.SQL(
                "GRANT SELECT, INSERT ON TABLE public.v4_legacy_publication_recovery_holds TO {}"
            ).format(psycopg.sql.Identifier(role))
        )
    return role


async def _publication_graph(database, tmp_path, monkeypatch):
    from test_v4_legacy_publication import (
        _hard_exit_publication_worker,
        _publication_fixture,
    )

    monkeypatch.setenv("TEST_DATABASE_URL", database)
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    fixture = await _publication_fixture(database)
    submitted = fixture["bridge"].submit_publication(fixture["command"])
    assert await _hard_exit_publication_worker(
        fixture, tmp_path, after_acceptance=True
    ) == 73
    with psycopg.connect(database) as connection:
        connection.execute(
            """UPDATE v4_legacy_publication_fixture_progress
               SET state='published', polls=2, updated_at=clock_timestamp()
               WHERE operation_id=%s AND state IN ('accepted','processing')""",
            (submitted["operation_id"],),
        )
    fixture["bridge"].close(
        submitted["cycle_id"],
        disposition="cancelled",
        reason="Preserve accepted publication liability while reading its final remote state",
    )
    return fixture, submitted


def _insert_0042_hold(database, operation_id):
    from test_v4_publication_recovery_holds import _INSERT_SQL, _hold_values

    with psycopg.connect(database) as connection:
        values = _hold_values(connection, operation_id)
        connection.execute(_INSERT_SQL, values)
        row = connection.execute(
            f"SELECT to_jsonb(hold) FROM {_HOLD_TABLE} hold WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0]
        return row


def _hold_contract(connection, role):
    table_owner = connection.execute(
        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=%s::regclass",
        (_HOLD_TABLE,),
    ).fetchone()[0]
    old_guard = connection.execute(
        "SELECT pg_get_functiondef('public.v4_guard_legacy_publication_recovery_hold()'::regprocedure)"
    ).fetchone()[0]
    immutable_guard = connection.execute(
        "SELECT pg_get_functiondef('public.v4_reject_legacy_publication_recovery_hold_change()'::regprocedure)"
    ).fetchone()[0]
    old_triggers = connection.execute(
        """SELECT tgname, pg_get_triggerdef(oid), tgenabled
           FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal
             AND tgname IN (
               'validate_legacy_publication_recovery_hold',
               'immutable_legacy_publication_recovery_hold'
             ) ORDER BY tgname""",
        (_HOLD_TABLE,),
    ).fetchall()
    old_constraints = connection.execute(
        """SELECT conname, pg_get_constraintdef(oid)
           FROM pg_constraint WHERE conrelid=%s::regclass
             AND conname NOT LIKE 'ck_v4_legacy_publication_recovery_holds_v1_%%'
           ORDER BY conname""",
        (_HOLD_TABLE,),
    ).fetchall()
    table_acl = connection.execute(
        """SELECT acl.grantee, acl.privilege_type, acl.is_grantable
           FROM pg_class relation,
                LATERAL aclexplode(COALESCE(relation.relacl, acldefault('r', relation.relowner))) acl
           WHERE relation.oid=%s::regclass
           ORDER BY acl.grantee, acl.privilege_type""",
        (_HOLD_TABLE,),
    ).fetchall()
    public_privileges = connection.execute(
        """SELECT count(*) FROM pg_class relation,
                  LATERAL aclexplode(COALESCE(relation.relacl, acldefault('r', relation.relowner))) acl
           WHERE relation.oid=%s::regclass AND acl.grantee=0""",
        (_HOLD_TABLE,),
    ).fetchone()[0]
    role_contract = connection.execute(
        """SELECT role.rolcanlogin,
                  has_schema_privilege(role.rolname,'public','USAGE'),
                  has_schema_privilege(role.rolname,'public','CREATE'),
                  has_table_privilege(role.rolname,%s,'SELECT'),
                  has_table_privilege(role.rolname,%s,'INSERT'),
                  has_table_privilege(role.rolname,%s,'UPDATE'),
                  has_table_privilege(role.rolname,%s,'DELETE'),
                  has_table_privilege(role.rolname,%s,'TRUNCATE'),
                  has_table_privilege(role.rolname,%s,'REFERENCES'),
                  has_table_privilege(role.rolname,%s,'TRIGGER')
           FROM pg_roles role WHERE role.rolname=%s""",
        (_HOLD_TABLE,) * 7 + (role,),
    ).fetchone()
    return {
        "owner": table_owner,
        "old_guard": old_guard,
        "immutable_guard": immutable_guard,
        "old_triggers": old_triggers,
        "old_constraints": old_constraints,
        "table_acl": table_acl,
        "public_privileges": public_privileges,
        "role": role_contract,
        "role_count": connection.execute("SELECT count(*) FROM pg_roles").fetchone()[0],
    }


def _assert_0043_schema_and_guard(connection, role):
    columns = connection.execute(
        """SELECT column_name, data_type, is_nullable
           FROM information_schema.columns WHERE table_schema='public'
             AND table_name='v4_legacy_publication_recovery_holds'
           ORDER BY ordinal_position"""
    ).fetchall()
    assert [column[0] for column in columns[-3:]] == [
        "liability_snapshot_version",
        "liability_snapshot_v1",
        "liability_fingerprint_v1",
    ]
    assert [(column[1], column[2]) for column in columns[-3:]] == [
        ("text", "YES"),
        ("jsonb", "YES"),
        ("text", "YES"),
    ]

    constraint_rows = connection.execute(
        """SELECT conname, pg_get_constraintdef(oid)
           FROM pg_constraint WHERE conrelid=%s::regclass
             AND conname LIKE 'ck_v4_legacy_publication_recovery_holds_v1_%%'
           ORDER BY conname""",
        (_HOLD_TABLE,),
    ).fetchall()
    constraints = dict(constraint_rows)
    assert set(constraints) == {
        "ck_v4_legacy_publication_recovery_holds_v1_all_or_none",
        "ck_v4_legacy_publication_recovery_holds_v1_version",
        "ck_v4_legacy_publication_recovery_holds_v1_snapshot_keys",
        "ck_v4_legacy_publication_recovery_holds_v1_fingerprint",
    }
    all_or_none = re.sub(r"\s+", " ", constraints["ck_v4_legacy_publication_recovery_holds_v1_all_or_none"]).lower()
    assert "liability_snapshot_version is null" in all_or_none
    assert "liability_snapshot_v1 is null" in all_or_none
    assert "liability_fingerprint_v1 is null" in all_or_none
    assert "liability_snapshot_version is not null" in all_or_none
    assert "liability_snapshot_v1 is not null" in all_or_none
    assert "liability_fingerprint_v1 is not null" in all_or_none
    assert "salience.publication-liability.v1" in constraints["ck_v4_legacy_publication_recovery_holds_v1_version"]
    snapshot_constraint = constraints["ck_v4_legacy_publication_recovery_holds_v1_snapshot_keys"]
    assert "jsonb_typeof" in snapshot_constraint and "?&" in snapshot_constraint
    assert " - ARRAY[" in snapshot_constraint and "'{}'::jsonb" in snapshot_constraint
    key_arrays = re.findall(r"ARRAY\[(.*?)\]", snapshot_constraint)
    assert len(key_arrays) == 2
    for key_array in key_arrays:
        assert tuple(re.findall(r"'([^']+)'(?:::text)?", key_array)) == _V1_KEYS
    assert "^[0-9a-f]{64}$" in constraints["ck_v4_legacy_publication_recovery_holds_v1_fingerprint"]

    function = connection.execute(
        "SELECT pg_get_functiondef('public.v4_guard_legacy_publication_recovery_hold_v1()'::regprocedure)"
    ).fetchone()[0]
    normalized = re.sub(r"\s+", "", function).lower()
    projection_start = function.index("jsonb_build_object(")
    projection_end = function.index(") INTO expected_snapshot", projection_start)
    projection = function[projection_start:projection_end]
    projection_keys = re.findall(
        r"'([a-z_]+)'\s*,\s*(?=(?:to_jsonb|COALESCE))", projection
    )
    assert tuple(projection_keys) == _V1_KEYS
    expected_pairs = (
        ("dispatch", "to_jsonb(binding)"),
        ("context", "to_jsonb(context)"),
        ("cycle", "to_jsonb(cycle)"),
        ("job", "to_jsonb(job)"),
        ("permit_claim", "to_jsonb(claim)"),
        ("permit", "to_jsonb(permit)"),
        ("fixture_receipt", "to_jsonb(receipt_row)"),
        ("fixture_progress", "to_jsonb(progress)"),
        ("publication_request", "to_jsonb(pub_request)"),
        ("publication_plan", "to_jsonb(plan)"),
        ("publication_attempt", "to_jsonb(publication_attempt_row.*)"),
        ("external_effect", "coalesce(to_jsonb(effect),'null'::jsonb)"),
        ("budget_reservation", "coalesce(to_jsonb(reservation),'null'::jsonb)"),
        ("output", "to_jsonb(job.output_payload)"),
    )
    for key, expression in expected_pairs:
        assert f"'{key}',{expression}" in normalized
    for key, relation, alias, filter_sql, order_sql in (
        (
            "remote_publication_receipts",
            "remote_publication_receipts",
            "remote_receipt",
            "whereremote_receipt.publication_attempt_id=publication_attempt_row.id",
            "orderbyremote_receipt.created_at,remote_receipt.id",
        ),
        (
            "publication_status_events",
            "publication_status_events",
            "status_event",
            "wherestatus_event.publication_attempt_id=publication_attempt_row.id",
            "orderbystatus_event.sequence_no,status_event.id",
        ),
        (
            "cycle_events",
            "v4_cycle_events",
            "cycle_event",
            "wherecycle_event.cycle_id=cycle.id",
            "orderbycycle_event.created_at,cycle_event.id",
        ),
        (
            "cycle_outbox",
            "v4_cycle_outbox",
            "outbox",
            "whereoutbox.cycle_id=cycle.id",
            "orderbyoutbox.sequence,outbox.id",
        ),
        (
            "job_checkpoints",
            "job_checkpoints",
            "checkpoint",
            "wherecheckpoint.job_id=job.id",
            "orderbycheckpoint.sequence_no,checkpoint.id",
        ),
        (
            "audit_events",
            "audit_events",
            "audit_event",
            "whereaudit_event.job_id=job.id",
            "orderbyaudit_event.created_at,audit_event.sequence_no,audit_event.id",
        ),
        (
            "provenance_records",
            "provenance_records",
            "provenance",
            "whereprovenance.job_id=job.id",
            "orderbyprovenance.created_at,provenance.id",
        ),
    ):
        expected_collection = (
            f"'{key}',coalesce((selectjsonb_agg(to_jsonb({alias})"
            f"orderby{order_sql.removeprefix('orderby')})frompublic.{relation}{alias}"
            f"{filter_sql}),'[]'::jsonb)"
        )
        assert expected_collection in normalized
    assert "encode(public.digest(new.liability_snapshot_v1::text,'sha256'),'hex')" in normalized
    assert "new.liability_snapshot_v1isdistinctfromexpected_snapshot" in normalized
    assert "tg_relid<>'public.v4_legacy_publication_recovery_holds'::regclass" in normalized
    assert "tg_table_schema<>'public'" in normalized
    assert "tg_table_name<>'v4_legacy_publication_recovery_holds'" in normalized
    proc = connection.execute(
        """SELECT prosecdef, proconfig, pg_get_userbyid(proowner)
           FROM pg_proc WHERE oid='public.v4_guard_legacy_publication_recovery_hold_v1()'::regprocedure"""
    ).fetchone()
    assert proc[0] is True
    assert proc[1] == ["search_path=pg_catalog, public"]
    table_owner = connection.execute(
        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=%s::regclass",
        (_HOLD_TABLE,),
    ).fetchone()[0]
    assert proc[2] == table_owner
    public_execute = connection.execute(
        """SELECT count(*) FROM pg_proc function,
                  LATERAL aclexplode(COALESCE(function.proacl, acldefault('f', function.proowner))) acl
           WHERE function.oid='public.v4_guard_legacy_publication_recovery_hold_v1()'::regprocedure
             AND acl.grantee=0 AND acl.privilege_type='EXECUTE'"""
    ).fetchone()[0]
    assert public_execute == 0
    assert connection.execute(
        "SELECT has_function_privilege(%s, 'public.v4_guard_legacy_publication_recovery_hold_v1()', 'EXECUTE')",
        (role,),
    ).fetchone()[0] is False
    trigger = connection.execute(
        """SELECT pg_get_triggerdef(oid) FROM pg_trigger
           WHERE tgrelid=%s::regclass AND tgname='validate_legacy_publication_recovery_hold_v1'
             AND NOT tgisinternal""",
        (_HOLD_TABLE,),
    ).fetchone()[0]
    assert "BEFORE INSERT" in trigger
    assert "v4_guard_legacy_publication_recovery_hold_v1" in trigger


def test_v1_snapshot_application_projection_matches_0043_guard():
    from salience.cycles import publication_recovery

    migration_source = (
        _MIGRATION_ROOT
        / "migrations/versions/0043_complete_publication_recovery_liabilities.py"
    ).read_text()
    projection_start = migration_source.index("SELECT jsonb_build_object(")
    projection_end = migration_source.index(
        ") INTO expected_snapshot", projection_start
    )
    migration_projection = migration_source[projection_start:projection_end]
    application_projection = publication_recovery._LIABILITY_SNAPSHOT_V1_SELECT

    application_keys = tuple(
        re.findall(
            r"^\s*'([a-z_]+)'\s*,",
            application_projection,
            flags=re.MULTILINE,
        )
    )
    assert application_keys == _V1_KEYS
    normalize = lambda value: re.sub(r"\s+", "", value).lower()
    assert normalize(migration_projection + ")").removeprefix("select") == normalize(
        application_projection
    )


async def _insert_fixture_hold_case(database, tmp_path, monkeypatch, *, legacy_row=False):
    before = _migration(database, "upgrade", _REVISION_0041)
    assert before.returncode == 0, before.stderr
    old_schema = _migration(database, "upgrade", _REVISION_0042)
    assert old_schema.returncode == 0, old_schema.stderr
    fixture, submitted = await _publication_graph(database, tmp_path, monkeypatch)
    if legacy_row:
        _insert_0042_hold(database, submitted["operation_id"])
    upgraded = _migration(database, "upgrade", _REVISION_0043)
    assert upgraded.returncode == 0, upgraded.stderr
    return fixture, submitted


def _alembic_version_num_type(connection):
    return connection.execute(
        """SELECT data_type, character_maximum_length
           FROM information_schema.columns WHERE table_schema='public'
             AND table_name='alembic_version' AND column_name='version_num'"""
    ).fetchone()


@pytest.mark.asyncio
async def test_0043_upgrade_preserves_populated_0042_rows_and_contract(
    monkeypatch, tmp_path
):
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, "publication_0043_upgrade_")
    role = None
    try:
        old_schema = _migration(database, "upgrade", _REVISION_0042)
        assert old_schema.returncode == 0, old_schema.stderr
        _, submitted = await _publication_graph(database, tmp_path, monkeypatch)
        legacy_row = _insert_0042_hold(database, submitted["operation_id"])
        role = _fixture_role(database)

        with psycopg.connect(database) as connection:
            assert _alembic_version_num_type(connection) == ("character varying", 32)
            old_columns = connection.execute(
                """SELECT column_name, data_type, is_nullable
                   FROM information_schema.columns WHERE table_schema='public'
                     AND table_name='v4_legacy_publication_recovery_holds'
                   ORDER BY ordinal_position"""
            ).fetchall()
            old_contract = _hold_contract(connection, role)
            old_count = connection.execute(
                f"SELECT count(*) FROM {_HOLD_TABLE}"
            ).fetchone()[0]

        upgraded = _migration(database, "upgrade", _REVISION_0043)
        assert upgraded.returncode == 0, upgraded.stderr

        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0043
            assert _alembic_version_num_type(connection) == ("character varying", 64)
            row_after = connection.execute(
                f"SELECT to_jsonb(hold) FROM {_HOLD_TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0]
            assert {key: value for key, value in row_after.items() if key not in {
                "liability_snapshot_version",
                "liability_snapshot_v1",
                "liability_fingerprint_v1",
            }} == legacy_row
            assert row_after["liability_snapshot_version"] is None
            assert row_after["liability_snapshot_v1"] is None
            assert row_after["liability_fingerprint_v1"] is None
            assert connection.execute(
                f"SELECT count(*) FROM {_HOLD_TABLE}"
            ).fetchone()[0] == old_count
            new_columns = connection.execute(
                """SELECT column_name, data_type, is_nullable
                   FROM information_schema.columns WHERE table_schema='public'
                     AND table_name='v4_legacy_publication_recovery_holds'
                   ORDER BY ordinal_position"""
            ).fetchall()
            assert [row for row in new_columns if row[0] not in {
                "liability_snapshot_version",
                "liability_snapshot_v1",
                "liability_fingerprint_v1",
            }] == old_columns
            assert _hold_contract(connection, role) == old_contract
            assert old_contract["public_privileges"] == 0
            assert old_contract["role"] == (
                False,
                True,
                False,
                True,
                True,
                False,
                False,
                False,
                False,
                False,
            )
            _assert_0043_schema_and_guard(connection, role)
            with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
                connection.execute(
                    f"UPDATE {_HOLD_TABLE} SET liability_snapshot_version='bad' WHERE operation_id=%s",
                    (submitted["operation_id"],),
                )
            with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
                connection.execute(
                    f"DELETE FROM {_HOLD_TABLE} WHERE operation_id=%s",
                    (submitted["operation_id"],),
                )
            assert connection.execute(
                f"SELECT to_jsonb(hold) FROM {_HOLD_TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0] == row_after
    finally:
        _drop_migration_database(source, name, role)


@pytest.mark.asyncio
async def test_0043_v1_guard_denies_public_and_caller_owned_trigger_paths(
    monkeypatch, tmp_path
):
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, "publication_0043_acl_")
    role = None
    try:
        _, submitted = await _insert_fixture_hold_case(
            database, tmp_path, monkeypatch
        )
        role = _fixture_role(database)
        with psycopg.connect(database) as connection:
            _assert_0043_schema_and_guard(connection, role)
            with connection.transaction():
                connection.execute(
                    psycopg.sql.SQL("SET LOCAL ROLE {}").format(
                        psycopg.sql.Identifier(role)
                    )
                )
                connection.execute(
                    "CREATE TEMP TABLE caller_hold AS "
                    "SELECT * FROM public.v4_legacy_publication_recovery_holds WITH NO DATA"
                )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute(
                            """CREATE TRIGGER caller_hold_v1_guard BEFORE INSERT ON caller_hold
                               FOR EACH ROW EXECUTE FUNCTION
                                 public.v4_guard_legacy_publication_recovery_hold_v1()"""
                        )
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute(
                            "SELECT public.v4_guard_legacy_publication_recovery_hold_v1()"
                        )
                connection.execute("RESET ROLE")

            connection.execute(
                "CREATE TEMP TABLE owner_hold AS "
                "SELECT * FROM public.v4_legacy_publication_recovery_holds WITH NO DATA"
            )
            connection.execute(
                """CREATE TRIGGER owner_hold_v1_guard BEFORE INSERT ON owner_hold
                   FOR EACH ROW EXECUTE FUNCTION
                     public.v4_guard_legacy_publication_recovery_hold_v1()"""
            )
            with pytest.raises(psycopg.Error, match="target"):
                with connection.transaction():
                    connection.execute("INSERT INTO owner_hold DEFAULT VALUES")
    finally:
        _drop_migration_database(source, name, role)


@pytest.mark.parametrize(
    "case_id",
    ["missing-key", "extra-key", "bad-tag", "bad-fingerprint", "current-row-mismatch", "null-v1"],
    ids=["missing-key", "extra-key", "bad-tag", "bad-fingerprint", "current-row-mismatch", "null-v1"],
)
@pytest.mark.asyncio
async def test_0043_insert_guard_rejects_invalid_v1_projection(
    case_id, monkeypatch, tmp_path
):
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, "publication_0043_insert_")
    try:
        _, submitted = await _insert_fixture_hold_case(
            database, tmp_path, monkeypatch
        )
        from test_v4_publication_recovery_holds import (
            _INSERT_V1_SQL,
            _hold_values_v1,
        )

        messages = {
            "missing-key": "21 keys",
            "extra-key": "21 keys",
            "bad-tag": "version",
            "bad-fingerprint": "fingerprint",
            "current-row-mismatch": "snapshot mismatch",
            "null-v1": "complete v1",
        }
        with psycopg.connect(database) as connection:
            values = list(_hold_values_v1(connection, submitted["operation_id"]))
            if case_id in {"missing-key", "extra-key", "current-row-mismatch"}:
                snapshot = dict(values[22].obj)
                if case_id == "missing-key":
                    del snapshot["provenance_records"]
                elif case_id == "extra-key":
                    snapshot["unexpected_liability"] = []
                else:
                    snapshot["job_checkpoints"] = [{"id": "not-the-current-row"}]
                values[22] = Jsonb(snapshot)
                values[23] = connection.execute(
                    "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex')",
                    (Jsonb(snapshot),),
                ).fetchone()[0]
            elif case_id == "bad-tag":
                values[21] = "salience.publication-liability.v2"
            elif case_id == "bad-fingerprint":
                values[23] = "0" * 64
            elif case_id == "null-v1":
                values[21:24] = [None, None, None]

            with pytest.raises(psycopg.Error, match=messages[case_id]), connection.transaction():
                connection.execute(_INSERT_V1_SQL, tuple(values))
            assert connection.execute(
                f"SELECT count(*) FROM {_HOLD_TABLE} WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0] == 0
    finally:
        _drop_migration_database(source, name)


@pytest.mark.asyncio
async def test_0043_downgrade_refuses_populated_v1_without_mutation(
    monkeypatch, tmp_path
):
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, "publication_0043_down_v1_")
    try:
        _, submitted = await _insert_fixture_hold_case(
            database, tmp_path, monkeypatch
        )
        from test_v4_publication_recovery_holds import (
            _INSERT_V1_SQL,
            _hold_values_v1,
        )

        with psycopg.connect(database) as connection:
            connection.execute(
                _INSERT_V1_SQL,
                _hold_values_v1(connection, submitted["operation_id"]),
            )
            before_row = connection.execute(
                f"SELECT to_jsonb(hold) FROM {_HOLD_TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0]

        blocker = psycopg.connect(database)
        try:
            blocker.execute(
                f"LOCK TABLE {_HOLD_TABLE} IN ROW EXCLUSIVE MODE"
            )
            started = time.monotonic()
            blocked = _migration(database, "downgrade", _REVISION_0042)
            lock_wait = time.monotonic() - started
        finally:
            blocker.rollback()
            blocker.close()
        assert blocked.returncode != 0
        assert "lock timeout" in blocked.stderr.lower()
        assert 2 <= lock_wait < 15

        refused = _migration(database, "downgrade", _REVISION_0042)
        assert refused.returncode != 0
        assert "preserve" in refused.stderr.lower()
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0043
            assert connection.execute(
                f"SELECT to_jsonb(hold) FROM {_HOLD_TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0] == before_row
            assert connection.execute(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='v4_legacy_publication_recovery_holds' AND column_name IN ('liability_snapshot_version','liability_snapshot_v1','liability_fingerprint_v1')"
            ).fetchone()[0] == 3

        revision_path = (
            _MIGRATION_ROOT
            / "migrations/versions/0043_complete_publication_recovery_liabilities.py"
        )
        migration_source = revision_path.read_text()
        assert 'revision = "0043_complete_publication_recovery_liabilities"' in migration_source
        assert 'down_revision = "0042_publication_recovery_holds"' in migration_source
        downgrade_source = migration_source.split("def downgrade():", 1)[1]
        normalized = re.sub(r"\s+", " ", downgrade_source.lower())
        order = (
            "set local lock_timeout='3s'",
            "lock table public.v4_legacy_publication_recovery_holds in share row exclusive mode",
            "if exists",
            "drop trigger",
        )
        positions = [normalized.index(token) for token in order]
        assert positions == sorted(positions)
        for field in (
            "liability_snapshot_version",
            "liability_snapshot_v1",
            "liability_fingerprint_v1",
        ):
            assert f"{field} is not null" in normalized
    finally:
        _drop_migration_database(source, name)


@pytest.mark.asyncio
async def test_0043_downgrade_without_v1_preserves_legacy_rows(
    monkeypatch, tmp_path
):
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, "publication_0043_down_legacy_")
    role = None
    try:
        before = _migration(database, "upgrade", _REVISION_0041)
        assert before.returncode == 0, before.stderr
        old_schema = _migration(database, "upgrade", _REVISION_0042)
        assert old_schema.returncode == 0, old_schema.stderr
        _, submitted = await _publication_graph(database, tmp_path, monkeypatch)
        old_row = _insert_0042_hold(database, submitted["operation_id"])
        role = _fixture_role(database)
        with psycopg.connect(database) as connection:
            assert _alembic_version_num_type(connection) == ("character varying", 32)
            old_contract = _hold_contract(connection, role)

        upgraded = _migration(database, "upgrade", _REVISION_0043)
        assert upgraded.returncode == 0, upgraded.stderr
        with psycopg.connect(database) as connection:
            assert _alembic_version_num_type(connection) == ("character varying", 64)
            row_at_0043 = connection.execute(
                f"SELECT to_jsonb(hold) FROM {_HOLD_TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0]
            assert row_at_0043["liability_snapshot_version"] is None
            assert row_at_0043["liability_snapshot_v1"] is None
            assert row_at_0043["liability_fingerprint_v1"] is None
            assert {
                key: value
                for key, value in row_at_0043.items()
                if key not in {
                    "liability_snapshot_version",
                    "liability_snapshot_v1",
                    "liability_fingerprint_v1",
                }
            } == old_row

        rolled_back = _migration(database, "downgrade", _REVISION_0042)
        assert rolled_back.returncode == 0, rolled_back.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0042
            assert _alembic_version_num_type(connection) == ("character varying", 64)
            assert _hold_contract(connection, role) == old_contract
            assert connection.execute(
                f"SELECT to_jsonb(hold) FROM {_HOLD_TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0] == old_row
            assert connection.execute(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='v4_legacy_publication_recovery_holds' AND column_name IN ('liability_snapshot_version','liability_snapshot_v1','liability_fingerprint_v1')"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT to_regprocedure('public.v4_guard_legacy_publication_recovery_hold_v1()')"
            ).fetchone()[0] is None

        legacy_refusal = _migration(database, "downgrade", _REVISION_0041)
        assert legacy_refusal.returncode != 0
        assert "preserve immutable publication recovery hold history" in legacy_refusal.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0042
            assert _alembic_version_num_type(connection) == ("character varying", 64)
            assert _hold_contract(connection, role) == old_contract
            assert connection.execute(
                f"SELECT to_jsonb(hold) FROM {_HOLD_TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0] == old_row
    finally:
        _drop_migration_database(source, name, role)


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
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from salience.cycles.admission import CycleAdmission
    from salience.cycles.contracts import GoalSpec
    from test_v4_cycle_admission import approved_goal, intent

    forged_owner = owner_mode != "original"
    expected_source_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
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
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == ("0031_runtime_waits" if forged_owner else expected_source_head)
        finally:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",(name,))
            admin.execute(psycopg.sql.SQL("DROP DATABASE {}").format(psycopg.sql.Identifier(name)))


_0044_CANONICAL_TABLES = {
    "approval_requests",
    "budgets",
    "content_programs",
    "identity_subjects",
    "permission_grants",
    "policy_versions",
    "workspaces",
}


def _capture_0043_fixture_contract(connection, baseline=None):
    """Capture pre-0044 fixture rows and the contracts later migrations must retain."""
    if baseline is None:
        table_names = connection.execute(
            """SELECT relation.relname FROM pg_class relation
               JOIN pg_namespace schema ON schema.oid=relation.relnamespace
               WHERE schema.nspname='public' AND relation.relkind IN ('r','p')
                 AND relation.relname <> 'alembic_version'
                 AND (left(relation.relname,3)='v4_'
                      OR relation.relname = ANY(%s))
               ORDER BY relation.relname""",
            (sorted(_0044_CANONICAL_TABLES),),
        ).fetchall()
        table_names = [row[0] for row in table_names]
        function_rows = connection.execute(
            """SELECT function.proname,
                      pg_get_function_identity_arguments(function.oid),
                      pg_get_functiondef(function.oid), function.prosecdef,
                      function.proconfig, pg_get_userbyid(function.proowner),
                      COALESCE(function.proacl,
                               acldefault('f', function.proowner))::text
               FROM pg_proc function JOIN pg_namespace schema
                 ON schema.oid=function.pronamespace
               WHERE schema.nspname='public' AND function.prokind='f'
               ORDER BY function.proname,
                        pg_get_function_identity_arguments(function.oid)"""
        ).fetchall()
        role_rows = connection.execute(
            """SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolcanlogin
               FROM pg_roles ORDER BY rolname"""
        ).fetchall()
    else:
        table_names = list(baseline["tables"])
        function_rows = [
            connection.execute(
                """SELECT function.proname,
                          pg_get_function_identity_arguments(function.oid),
                          pg_get_functiondef(function.oid), function.prosecdef,
                          function.proconfig, pg_get_userbyid(function.proowner),
                          COALESCE(function.proacl,
                                   acldefault('f', function.proowner))::text
                   FROM pg_proc function JOIN pg_namespace schema
                     ON schema.oid=function.pronamespace
                   WHERE schema.nspname='public' AND function.prokind='f'
                     AND function.proname=%s
                     AND pg_get_function_identity_arguments(function.oid)=%s""",
                signature,
            ).fetchone()
            for signature in baseline["functions"]
        ]
        role_rows = [
            connection.execute(
                """SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolcanlogin
                   FROM pg_roles WHERE rolname=%s""",
                (role_name,),
            ).fetchone()
            for role_name in baseline["roles"]
        ]

    tables = {}
    for table_name in table_names:
        columns = connection.execute(
            """SELECT column_name, data_type, udt_name, is_nullable,
                      column_default, ordinal_position
               FROM information_schema.columns
               WHERE table_schema='public' AND table_name=%s
               ORDER BY ordinal_position""",
            (table_name,),
        ).fetchall()
        constraints = connection.execute(
            """SELECT conname, pg_get_constraintdef(oid)
               FROM pg_constraint WHERE conrelid=%s::regclass
               ORDER BY conname""",
            (f"public.{table_name}",),
        ).fetchall()
        indexes = connection.execute(
            """SELECT indexname, indexdef FROM pg_indexes
               WHERE schemaname='public' AND tablename=%s
               ORDER BY indexname""",
            (table_name,),
        ).fetchall()
        trigger_names = None
        if baseline is not None:
            trigger_names = baseline["tables"][table_name]["trigger_names"]
        triggers = connection.execute(
            """SELECT tgname, pg_get_triggerdef(oid), tgenabled
               FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal
                 AND (%s::text[] IS NULL OR tgname=ANY(%s::text[]))
               ORDER BY tgname""",
            (f"public.{table_name}", trigger_names, trigger_names),
        ).fetchall()
        rows = connection.execute(
            psycopg.sql.SQL(
                "SELECT COALESCE(jsonb_agg(row_data ORDER BY row_data::text), '[]'::jsonb) "
                "FROM (SELECT to_jsonb(row_value) AS row_data FROM public.{} row_value) rows"
            ).format(psycopg.sql.Identifier(table_name))
        ).fetchone()[0]
        tables[table_name] = {
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
            "triggers": triggers,
            "trigger_names": [row[0] for row in triggers],
            "rows": rows,
        }

    return {
        "tables": tables,
        "functions": [(row[0], row[1]) for row in function_rows if row is not None],
        "function_rows": [row for row in function_rows if row is not None],
        "roles": [row[0] for row in role_rows if row is not None],
        "role_rows": [row for row in role_rows if row is not None],
    }


def _assert_0043_fixture_contract(connection, before):
    after = _capture_0043_fixture_contract(connection, before)
    for table_name, contract in before["tables"].items():
        assert after["tables"][table_name] == contract
    assert after["function_rows"] == before["function_rows"]
    assert after["role_rows"] == before["role_rows"]


def _seed_0044_workspace_subject(connection):
    workspace_id = uuid4()
    subject_id = uuid4()
    connection.execute(
        "INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'0044 fixture')",
        (workspace_id, "p1-0044-" + uuid4().hex),
    )
    connection.execute(
        """INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at)
           VALUES(%s,%s,'https://fixture.invalid',%s,clock_timestamp()+interval '1 day')""",
        (subject_id, workspace_id, str(subject_id)),
    )
    return workspace_id, subject_id


def _seed_0044_cycle(connection, workspace_id, subject_id):
    goal_id = uuid4()
    program_id = uuid4()
    intent_id = uuid4()
    cycle_id = uuid4()
    context_id = uuid4()
    operation_id = uuid4()
    connection.execute(
        """INSERT INTO content_programs(id,workspace_id,slug,name,niche)
           VALUES(%s,%s,%s,'0044 program','fixture')""",
        (program_id, workspace_id, "p1-0044-" + uuid4().hex),
    )
    connection.execute(
        "INSERT INTO v4_goals(id,workspace_id,state) VALUES(%s,%s,'active')",
        (goal_id, workspace_id),
    )
    connection.execute(
        """INSERT INTO v4_goal_revisions(goal_id,revision,payload)
           VALUES(%s,1,%s)""",
        (goal_id, Jsonb({"schema_version": "GoalSpec.local.v1"})),
    )
    connection.execute(
        """INSERT INTO v4_cycle_intents(id,goal_id,goal_revision,slot,fingerprint,
                  due_at,expires_at)
           VALUES(%s,%s,1,%s,%s,clock_timestamp()-interval '1 minute',
                  clock_timestamp()+interval '1 day')""",
        (intent_id, goal_id, str(intent_id), "a" * 64),
    )
    connection.execute("SET CONSTRAINTS ALL DEFERRED")
    connection.execute(
        """INSERT INTO v4_cycles(id,intent_id,context_id,operation_id,state)
           VALUES(%s,%s,%s,%s,'runnable')""",
        (cycle_id, intent_id, context_id, operation_id),
    )
    connection.execute(
        """INSERT INTO v4_run_contexts(id,cycle_id,payload)
           VALUES(%s,%s,%s)""",
        (
            context_id,
            cycle_id,
            Jsonb(
                {
                    "schema_version": "RunContext.local.v1",
                    "content_program_id": str(program_id),
                    "subject_id": str(subject_id),
                }
            ),
        ),
    )
    return {
        "goal_id": goal_id,
        "program_id": program_id,
        "intent_id": intent_id,
        "cycle_id": cycle_id,
        "context_id": context_id,
        "operation_id": operation_id,
    }


def _seed_0044_policy(connection, workspace_id, *, status="draft", version="1"):
    policy_id = uuid4()
    document = {
        "schema_version": "salience.p1-policy.v1",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    connection.execute(
        """INSERT INTO policy_versions(id,workspace_id,policy_name,version,status,
                  document,document_hash,jurisdiction_refs)
           VALUES(%s,%s,'production-effect',%s,%s,%s,%s,%s)""",
        (
            policy_id,
            workspace_id,
            version,
            status,
            Jsonb(document),
            "b" * 64,
            Jsonb({"jurisdictions": ["fixture"]}),
        ),
    )
    return policy_id


def _approval_context_0044(workspace_id, subject_id, cycle, policy_id):
    return {
        "schema_version": "salience.p1-effect-approval.v1",
        "workspace_id": str(workspace_id),
        "program_id": str(cycle["program_id"]),
        "goal_id": str(cycle["goal_id"]),
        "goal_revision": 1,
        "intent_id": str(cycle["intent_id"]),
        "cycle_id": str(cycle["cycle_id"]),
        "context_id": str(cycle["context_id"]),
        "operation_id": str(cycle["operation_id"]),
        "subject_id": str(subject_id),
        "effect_type": "publication",
        "purpose": "publish-approved-artifact",
        "artifact_sha256": "c" * 64,
        "account_id": "production-account-1",
        "provider_id": "production-provider-1",
        "policy_version_id": str(policy_id),
        "policy_document_hash": "b" * 64,
        "expires_at": "2099-01-01T00:00:00Z",
    }


def _insert_0044_approval(connection, workspace_id, policy_id, context):
    approval_id = uuid4()
    connection.execute(
        """INSERT INTO approval_requests(
                  id,workspace_id,policy_version_id,effect_type,status,
                  request_context,requested_by)
           VALUES(%s,%s,%s,'publication','pending',%s,'policy-owner')""",
        (approval_id, workspace_id, policy_id, Jsonb(context)),
    )
    return approval_id


def _prepare_0044_database(prefix, *, seed=None):
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, prefix)
    try:
        upgraded_0043 = _migration(database, "upgrade", _REVISION_0043)
        assert upgraded_0043.returncode == 0, upgraded_0043.stdout + upgraded_0043.stderr
        with psycopg.connect(database) as connection:
            if seed is not None:
                seed(connection)
            before = _capture_0043_fixture_contract(connection)
        upgraded_0044 = _migration(database, "upgrade", _REVISION_0044)
        assert upgraded_0044.returncode == 0, upgraded_0044.stdout + upgraded_0044.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0044
            _assert_0043_fixture_contract(connection, before)
        return name, database, before
    except Exception:
        _drop_migration_database(source, name)
        raise


def test_0044_upgrade_preserves_0043_fixture_rows():
    def seed(connection):
        workspace_id, subject_id = _seed_0044_workspace_subject(connection)
        connection.execute(
            """INSERT INTO v4_stop_scopes(workspace_id,scope_key,changed_by,reason)
               VALUES(%s,'workspace',%s,'0043 fixture row')""",
            (workspace_id, subject_id),
        )

    source = os.environ["TEST_DATABASE_URL"]
    name, database, before = _prepare_0044_database(
        "p1_0044_fixture_preserve_", seed=seed
    )
    try:
        with psycopg.connect(database) as connection:
            _assert_0043_fixture_contract(connection, before)
    finally:
        _drop_migration_database(source, name)


def test_0044_grant_mutation_advances_subject_revision():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _ = _prepare_0044_database(
        "p1_0044_grant_revision_",
        seed=lambda connection: _seed_0044_workspace_subject(connection),
    )
    try:
        with psycopg.connect(database) as connection:
            workspace_id, subject_id = connection.execute(
                "SELECT workspace_id,id FROM identity_subjects ORDER BY id LIMIT 1"
            ).fetchone()
            second_workspace_id, second_subject_id = _seed_0044_workspace_subject(
                connection
            )
            before_insert = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s", (subject_id,)
            ).fetchone()[0]
            grant_id = connection.execute(
                """INSERT INTO permission_grants(
                          workspace_id,principal_type,principal_id,scope,effect,
                          constraints,expires_at)
                   VALUES(%s,'identity',%s,'publication:publish','allow','{}'::jsonb,
                          clock_timestamp()+interval '1 hour') RETURNING id""",
                (workspace_id, str(subject_id)),
            ).fetchone()[0]
            after_insert = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s", (subject_id,)
            ).fetchone()[0]
            assert after_insert == before_insert + 1
            connection.execute(
                "UPDATE permission_grants SET effect='deny' WHERE id=%s", (grant_id,)
            )
            after_update = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s", (subject_id,)
            ).fetchone()[0]
            assert after_update == after_insert + 1
            second_subject_before = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s",
                (second_subject_id,),
            ).fetchone()[0]
            connection.execute(
                """UPDATE permission_grants
                   SET workspace_id=%s,principal_id=%s,scope='publication:publish:reviewed',
                       constraints='{\"purpose\":\"reviewed\"}'::jsonb,
                       expires_at=clock_timestamp()+interval '2 hours'
                   WHERE id=%s""",
                (second_workspace_id, str(second_subject_id), grant_id),
            )
            after_move = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s", (subject_id,)
            ).fetchone()[0]
            second_subject_after_move = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s",
                (second_subject_id,),
            ).fetchone()[0]
            assert after_move == after_update + 1
            assert second_subject_after_move == second_subject_before + 1
            connection.execute("DELETE FROM permission_grants WHERE id=%s", (grant_id,))
            after_delete = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s",
                (second_subject_id,),
            ).fetchone()[0]
            assert after_delete == second_subject_after_move + 1
    finally:
        _drop_migration_database(source, name)


def test_0044_policy_and_tagged_approval_transitions_are_terminal():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _ = _prepare_0044_database("p1_0044_terminal_authority_")
    try:
        with psycopg.connect(database) as connection:
            workspace_id, subject_id = _seed_0044_workspace_subject(connection)
            cycle = _seed_0044_cycle(connection, workspace_id, subject_id)
            policy_id = _seed_0044_policy(connection, workspace_id)
            connection.execute(
                """UPDATE policy_versions SET document=%s,document_hash=%s,
                          effective_at=clock_timestamp(),jurisdiction_refs=%s
                   WHERE id=%s""",
                (
                    Jsonb({"schema_version": "salience.p1-policy.v1", "revision": 2}),
                    "a" * 64,
                    Jsonb({"jurisdictions": ["fixture", "us"]}),
                    policy_id,
                ),
            )
            connection.execute(
                "UPDATE policy_versions SET status='active' WHERE id=%s", (policy_id,)
            )
            with pytest.raises(psycopg.Error, match="activated policy fields are immutable"):
                with connection.transaction():
                    connection.execute(
                        "UPDATE policy_versions SET document_hash=%s WHERE id=%s",
                        ("f" * 64, policy_id),
                    )
            connection.execute(
                "UPDATE policy_versions SET status='revoked' WHERE id=%s", (policy_id,)
            )
            with pytest.raises(
                psycopg.Error,
                match="non-draft policy cannot return to draft or active",
            ):
                with connection.transaction():
                    connection.execute(
                        "UPDATE policy_versions SET status='active' WHERE id=%s",
                        (policy_id,),
                    )

            approval_policy_id = _seed_0044_policy(
                connection, workspace_id, status="active", version="2"
            )
            context = _approval_context_0044(
                workspace_id, subject_id, cycle, approval_policy_id
            )
            mismatched_job_context = {**context, "job_id": str(uuid4())}
            with pytest.raises(
                psycopg.Error, match="tagged effect approval job binding mismatch"
            ):
                with connection.transaction():
                    _insert_0044_approval(
                        connection,
                        workspace_id,
                        approval_policy_id,
                        mismatched_job_context,
                    )
            naive_expiry_context = {
                **context,
                "expires_at": "2099-01-01T00:00:00",
            }
            with pytest.raises(
                psycopg.Error,
                match="tagged effect approval expiry must be timezone-aware",
            ):
                with connection.transaction():
                    _insert_0044_approval(
                        connection,
                        workspace_id,
                        approval_policy_id,
                        naive_expiry_context,
                    )
            approval_id = _insert_0044_approval(
                connection, workspace_id, approval_policy_id, context
            )
            decided_at = connection.execute(
                """UPDATE approval_requests SET status='approved',decided_by='reviewer',
                          decided_at=clock_timestamp(),decision_reason='approved exact binding'
                   WHERE id=%s RETURNING decided_at""",
                (approval_id,),
            ).fetchone()[0]
            changed_binding = dict(context)
            changed_binding["purpose"] = "different-purpose"
            with pytest.raises(
                psycopg.Error, match="decided effect approval binding is immutable"
            ):
                with connection.transaction():
                    connection.execute(
                        "UPDATE approval_requests SET request_context=%s WHERE id=%s",
                        (Jsonb(changed_binding), approval_id),
                    )
            with pytest.raises(psycopg.Error, match="approval revocation event required"):
                with connection.transaction():
                    connection.execute(
                        "UPDATE approval_requests SET status='revoked' WHERE id=%s",
                        (approval_id,),
                    )
            event_payload = {
                "schema_version": "salience.p1-effect-approval-revocation.v1",
                "approval_request_id": str(approval_id),
                "operation_id": str(cycle["operation_id"]),
                "policy_version_id": str(approval_policy_id),
                "transaction_id": str(
                    connection.execute("SELECT txid_current()").fetchone()[0]
                ),
            }
            with connection.transaction():
                connection.execute(
                    """INSERT INTO v4_cycle_events(
                              id,workspace_id,subject_id,goal_id,intent_id,cycle_id,
                              kind,traceparent,payload)
                       VALUES(%s,%s,%s,%s,%s,%s,'production_approval_revoked',
                              '00-11111111111111111111111111111111-2222222222222222-01',%s)""",
                    (
                        uuid4(),
                        workspace_id,
                        subject_id,
                        cycle["goal_id"],
                        cycle["intent_id"],
                        cycle["cycle_id"],
                        Jsonb(event_payload),
                    ),
                )
                connection.execute(
                    "UPDATE approval_requests SET status='revoked' WHERE id=%s",
                    (approval_id,),
                )
            assert connection.execute(
                "SELECT decided_at FROM approval_requests WHERE id=%s", (approval_id,)
            ).fetchone()[0] == decided_at
            unrelated_id = connection.execute(
                """INSERT INTO approval_requests(
                          workspace_id,effect_type,status,request_context,requested_by)
                   VALUES(%s,'legacy','pending','{}'::jsonb,'legacy') RETURNING id""",
                (workspace_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE approval_requests SET status='approved' WHERE id=%s",
                (unrelated_id,),
            )
    finally:
        _drop_migration_database(source, name)


def _insert_0044_production_permit(connection, binding, *, generation=1):
    permit_id = uuid4()
    route = {
        "deployment_id": uuid4(),
        "environment_id": uuid4(),
        "route_id": uuid4(),
        "slot_id": uuid4(),
        "owner_id": uuid4(),
        "fencing_epoch": 7,
        "route_manifest_sha256": "d" * 64,
        "artifact_lock_sha256": "e" * 64,
        "route_fence_decision_id": uuid4(),
    }
    idempotency_key = "p1-0044-operation:" + str(binding["operation_id"])
    request = {
        "schema_version": "salience.p1-production-effect-permit.v1",
        "generation": generation,
        "workspace_id": str(binding["workspace_id"]),
        "goal_id": str(binding["cycle"]["goal_id"]),
        "goal_revision": 1,
        "program_id": str(binding["cycle"]["program_id"]),
        "intent_id": str(binding["cycle"]["intent_id"]),
        "cycle_id": str(binding["cycle"]["cycle_id"]),
        "context_id": str(binding["cycle"]["context_id"]),
        "operation_id": str(binding["operation_id"]),
        "subject_id": str(binding["subject_id"]),
        "subject_revision": 1,
        "grant_ids": [],
        "policy_version_id": str(binding["policy_id"]),
        "policy_document_hash": "b" * 64,
        "approval_request_id": str(binding["approval_id"]),
        "approval_expires_at": "2099-01-01T00:00:00+00:00",
        "effect_type": "publication",
        "purpose": "publish-approved-artifact",
        "account_id": "production-account-1",
        "provider_id": "production-provider-1",
        "artifact_sha256": "c" * 64,
        **{key: str(value) for key, value in route.items() if key != "fencing_epoch"},
        "fencing_epoch": route["fencing_epoch"],
        "idempotency_key": idempotency_key,
    }
    if binding.get("publication_request_id"):
        request["publication_request_id"] = str(binding["publication_request_id"])
    request.update(binding.get("publication_bindings", {}))
    connection.execute(
        """INSERT INTO v4_production_effect_permits(
                  permit_id,operation_id,generation,workspace_id,goal_id,goal_revision,
                  program_id,intent_id,cycle_id,context_id,subject_id,subject_revision,
                  grant_ids,policy_version_id,policy_document_hash,approval_request_id,
                  approval_expires_at,effect_type,purpose,account_id,provider_id,
                  artifact_sha256,deployment_id,environment_id,route_id,slot_id,owner_id,
                  fencing_epoch,route_manifest_sha256,artifact_lock_sha256,
                  route_fence_decision_id,issued_at,expires_at,request_snapshot)
                  VALUES(%(permit_id)s,%(operation_id)s,%(generation)s,%(workspace_id)s,
                  %(goal_id)s,1,%(program_id)s,%(intent_id)s,%(cycle_id)s,%(context_id)s,
                  %(subject_id)s,1,'[]'::jsonb,%(policy_version_id)s,%(policy_hash)s,
                  %(approval_id)s,
                  (SELECT (approval.request_context->>'expires_at')::timestamptz
                   FROM approval_requests approval WHERE approval.id=%(approval_id)s),
                  'publication',
                  'publish-approved-artifact','production-account-1',
                  'production-provider-1',%(artifact_hash)s,%(deployment_id)s,
                  %(environment_id)s,%(route_id)s,%(slot_id)s,%(owner_id)s,
                  %(fencing_epoch)s,%(route_manifest_sha256)s,%(artifact_lock_sha256)s,
                  %(route_fence_decision_id)s,clock_timestamp(),
                  clock_timestamp()+interval '30 seconds',%(request_snapshot)s)""",
        {
            "permit_id": permit_id,
            "operation_id": binding["operation_id"],
            "generation": generation,
            "workspace_id": binding["workspace_id"],
            "goal_id": binding["cycle"]["goal_id"],
            "program_id": binding["cycle"]["program_id"],
            "intent_id": binding["cycle"]["intent_id"],
            "cycle_id": binding["cycle"]["cycle_id"],
            "context_id": binding["cycle"]["context_id"],
            "subject_id": binding["subject_id"],
            "policy_version_id": binding["policy_id"],
            "policy_hash": "b" * 64,
            "approval_id": binding["approval_id"],
            "artifact_hash": "c" * 64,
            **route,
            "request_snapshot": Jsonb(request),
        },
    )
    return permit_id, idempotency_key


def test_0044_production_claim_is_unique_and_mode_separated():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _ = _prepare_0044_database("p1_0044_production_claim_")
    try:
        with psycopg.connect(database) as connection:
            workspace_id, subject_id = _seed_0044_workspace_subject(connection)
            cycle = _seed_0044_cycle(connection, workspace_id, subject_id)
            policy_id = _seed_0044_policy(connection, workspace_id, status="active")
            approval_context = _approval_context_0044(
                workspace_id, subject_id, cycle, policy_id
            )
            approval_id = _insert_0044_approval(
                connection, workspace_id, policy_id, approval_context
            )
            connection.execute(
                """UPDATE approval_requests SET status='approved',decided_by='reviewer',
                          decided_at=clock_timestamp() WHERE id=%s""",
                (approval_id,),
            )
            binding = {
                "workspace_id": workspace_id,
                "subject_id": subject_id,
                "operation_id": cycle["operation_id"],
                "cycle": cycle,
                "policy_id": policy_id,
                "approval_id": approval_id,
                "publication_bindings": {
                    "publication_request_id": str(uuid4()),
                    "job_id": str(uuid4()),
                    "workflow_id": "workflow-0044",
                    "workflow_run_id": "workflow-run-0044",
                    "run_id": "run-0044",
                    "publication_plan_id": str(uuid4()),
                    "publication_attempt_id": str(uuid4()),
                    "external_effect_id": str(uuid4()),
                    "effect_id": str(uuid4()),
                    "remote_receipt_id": str(uuid4()),
                    "receipt_id": str(uuid4()),
                },
            }
            permit_id, idempotency_key = _insert_0044_production_permit(
                connection, binding
            )
            permit_snapshot = connection.execute(
                "SELECT request_snapshot FROM v4_production_effect_permits WHERE permit_id=%s",
                (permit_id,),
            ).fetchone()[0]
            for key, value in binding["publication_bindings"].items():
                assert permit_snapshot[key] == value
            invalid_binding = {
                **binding,
                "publication_bindings": {"workflow_run_id": 17},
            }
            with pytest.raises(
                psycopg.Error,
                match="optional publication binding must be a string identifier",
            ):
                with connection.transaction():
                    _insert_0044_production_permit(
                        connection, invalid_binding, generation=2
                    )
            blank_binding = {
                **binding,
                "publication_bindings": {"workflow_run_id": "  "},
            }
            with pytest.raises(
                psycopg.Error,
                match="optional publication binding must be a string identifier",
            ):
                with connection.transaction():
                    _insert_0044_production_permit(
                        connection, blank_binding, generation=2
                    )
            with pytest.raises(
                psycopg.Error,
                match="duplicate key value violates unique constraint",
            ):
                with connection.transaction():
                    _insert_0044_production_permit(connection, binding)
            claim_id = uuid4()
            connection.execute(
                """INSERT INTO v4_production_effect_permit_claims(
                          claim_id,permit_id,operation_id,generation,
                          route_fence_decision_id)
                   SELECT %s,permit_id,operation_id,generation,route_fence_decision_id
                   FROM v4_production_effect_permits WHERE permit_id=%s""",
                (claim_id, permit_id),
            )
            with pytest.raises(
                psycopg.Error, match="duplicate key value violates unique constraint"
            ):
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO v4_production_effect_permit_claims(
                                  claim_id,permit_id,operation_id,generation,
                                  route_fence_decision_id)
                           SELECT %s,permit_id,operation_id,generation,route_fence_decision_id
                           FROM v4_production_effect_permits WHERE permit_id=%s""",
                        (uuid4(), permit_id),
                    )
            connection.execute(
                """SELECT public.v4_advance_production_current_dispatch_v1(
                          %s,0,%s,NULL,%s,NULL)""",
                (cycle["operation_id"], permit_id, idempotency_key),
            )
            connection.execute(
                """SELECT public.v4_advance_production_current_dispatch_v1(
                          %s,1,%s,%s,%s,NULL)""",
                (cycle["operation_id"], permit_id, claim_id, idempotency_key),
            )
            pointer = connection.execute(
                """SELECT generation,current_permit_id,current_claim_id,idempotency_key
                   FROM v4_production_current_dispatch WHERE operation_id=%s""",
                (cycle["operation_id"],),
            ).fetchone()
            assert pointer == (1, permit_id, claim_id, idempotency_key)
            with pytest.raises(
                psycopg.Error,
                match="production current dispatch may only change through v1 function",
            ):
                with connection.transaction():
                    connection.execute(
                        """UPDATE v4_production_current_dispatch
                           SET idempotency_key='direct-write-denied'
                           WHERE operation_id=%s""",
                        (cycle["operation_id"],),
                    )
            public_write_privileges = connection.execute(
                """SELECT count(*)
                   FROM pg_class relation,
                        LATERAL aclexplode(
                          COALESCE(relation.relacl, acldefault('r',relation.relowner))
                        ) acl
                   WHERE relation.oid=ANY(ARRAY[
                           'public.v4_production_effect_permits'::regclass,
                           'public.v4_production_effect_permit_claims'::regclass,
                           'public.v4_production_current_dispatch'::regclass
                         ])
                     AND acl.grantee=0
                     AND acl.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')"""
            ).fetchone()[0]
            assert public_write_privileges == 0
            public_function_execute = connection.execute(
                """SELECT count(*)
                   FROM pg_proc function,
                        LATERAL aclexplode(
                          COALESCE(function.proacl,
                                   acldefault('f',function.proowner))
                        ) acl
                   WHERE function.oid=ANY(ARRAY[
                           'public.v4_validate_production_effect_permit_v1()'::regprocedure,
                           'public.v4_reject_production_history_change_v1()'::regprocedure,
                           'public.v4_guard_production_current_dispatch_write_v1()'::regprocedure,
                           'public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)'::regprocedure,
                           'public.v4_advance_identity_subject_revision_from_grant_v1()'::regprocedure,
                           'public.v4_guard_policy_version_transition_v1()'::regprocedure,
                           'public.v4_guard_effect_approval_transition_v1()'::regprocedure,
                           'public.v4_production_policy_rollback_inventory_v1(uuid)'::regprocedure
                         ])
                     AND acl.grantee=0 AND acl.privilege_type='EXECUTE'"""
            ).fetchone()[0]
            assert public_function_execute == 0
            second_permit, _ = _insert_0044_production_permit(
                connection, binding, generation=2
            )
            with pytest.raises(
                psycopg.Error, match="typed_no_effect_command_required"
            ):
                with connection.transaction():
                    connection.execute(
                        """SELECT public.v4_advance_production_current_dispatch_v1(
                                  %s,1,%s,NULL,%s,NULL)""",
                        (cycle["operation_id"], second_permit, idempotency_key),
                    )
            fixture_claim_id = uuid4()
            fixture_permit_id = uuid4()
            connection.execute(
                """INSERT INTO v4_permit_claims(
                          id,cycle_id,operation_id,context_id,request_fingerprint)
                   VALUES(%s,%s,%s,%s,%s)""",
                (
                    fixture_claim_id,
                    cycle["cycle_id"],
                    cycle["operation_id"],
                    cycle["context_id"],
                    "1" * 64,
                ),
            )
            connection.execute(
                """INSERT INTO v4_dispatch_permits(
                          id,claim_id,version,idempotency_key,fingerprint,subject_id,
                          goal_revision,workspace_stop_revision,goal_stop_revision,
                          request,expires_at,traceparent)
                   VALUES(%s,%s,1,'fixture-key',%s,%s,1,1,1,%s,
                          clock_timestamp()+interval '1 minute',
                          '00-11111111111111111111111111111111-2222222222222222-01')""",
                (
                    fixture_permit_id,
                    fixture_claim_id,
                    "2" * 64,
                    subject_id,
                    Jsonb(
                        {
                            "context_id": str(cycle["context_id"]),
                            "operation_id": str(cycle["operation_id"]),
                            "effect": "fixture.noop",
                            "purpose": "fixture_execution",
                            "account_ref": "fixture-account",
                        }
                    ),
                ),
            )
            connection.execute(
                "UPDATE v4_permit_claims SET current_permit_id=%s,revision=2 WHERE id=%s",
                (fixture_permit_id, fixture_claim_id),
            )
            with pytest.raises(psycopg.Error, match="foreign key constraint"):
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO v4_production_effect_permit_claims(
                                  claim_id,permit_id,operation_id,generation,
                                  route_fence_decision_id)
                           VALUES(%s,%s,%s,1,%s)""",
                        (uuid4(), fixture_permit_id, uuid4(), uuid4()),
                    )
            with pytest.raises(psycopg.Error, match="foreign key constraint"):
                with connection.transaction():
                    connection.execute(
                        "UPDATE v4_permit_claims SET current_permit_id=%s,revision=3 WHERE id=%s",
                        (permit_id, fixture_claim_id),
                    )
                    connection.execute("SET CONSTRAINTS v4_current_permit_fk IMMEDIATE")
    finally:
        _drop_migration_database(source, name)


def test_0044_rollback_inventory_is_complete_and_read_only():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _ = _prepare_0044_database("p1_0044_rollback_inventory_")
    try:
        with psycopg.connect(database) as connection:
            workspace_id, subject_id = _seed_0044_workspace_subject(connection)
            cycle = _seed_0044_cycle(connection, workspace_id, subject_id)
            policy_id = _seed_0044_policy(connection, workspace_id, status="active")
            context = _approval_context_0044(workspace_id, subject_id, cycle, policy_id)
            approval_id = _insert_0044_approval(
                connection, workspace_id, policy_id, context
            )
            connection.execute(
                """UPDATE approval_requests SET status='approved',
                          decided_by='reviewer',decided_at=clock_timestamp()
                   WHERE id=%s""",
                (approval_id,),
            )
            binding = {
                "workspace_id": workspace_id,
                "subject_id": subject_id,
                "operation_id": cycle["operation_id"],
                "cycle": cycle,
                "policy_id": policy_id,
                "approval_id": approval_id,
            }
            production_permit_id, idempotency_key = _insert_0044_production_permit(
                connection, binding
            )
            production_claim_id = uuid4()
            connection.execute(
                """INSERT INTO v4_production_effect_permit_claims(
                          claim_id,permit_id,operation_id,generation,
                          route_fence_decision_id)
                   SELECT %s,permit_id,operation_id,generation,route_fence_decision_id
                   FROM v4_production_effect_permits WHERE permit_id=%s""",
                (production_claim_id, production_permit_id),
            )
            connection.execute(
                """SELECT public.v4_advance_production_current_dispatch_v1(
                          %s,0,%s,%s,%s,NULL)""",
                (
                    cycle["operation_id"],
                    production_permit_id,
                    production_claim_id,
                    idempotency_key,
                ),
            )
            fixture_claim_id = uuid4()
            fixture_permit_id = uuid4()
            fixture_previous_permit_id = uuid4()
            connection.execute(
                """INSERT INTO v4_permit_claims(
                          id,cycle_id,operation_id,context_id,request_fingerprint)
                   VALUES(%s,%s,%s,%s,%s)""",
                (
                    fixture_claim_id,
                    cycle["cycle_id"],
                    cycle["operation_id"],
                    cycle["context_id"],
                    "1" * 64,
                ),
            )
            connection.execute(
                """INSERT INTO v4_dispatch_permits(
                          id,claim_id,version,idempotency_key,fingerprint,subject_id,
                          goal_revision,workspace_stop_revision,goal_stop_revision,
                          request,expires_at,traceparent)
                   VALUES(%s,%s,1,'fixture-key-previous',%s,%s,1,1,1,%s,
                          clock_timestamp()+interval '1 minute',
                          '00-11111111111111111111111111111111-2222222222222222-01')""",
                (
                    fixture_previous_permit_id,
                    fixture_claim_id,
                    "3" * 64,
                    subject_id,
                    Jsonb(
                        {
                            "context_id": str(cycle["context_id"]),
                            "operation_id": str(cycle["operation_id"]),
                            "effect": "fixture.noop",
                            "purpose": "fixture_execution",
                            "account_ref": "fixture-account",
                        }
                    ),
                ),
            )
            connection.execute(
                """INSERT INTO v4_dispatch_permits(
                          id,claim_id,version,idempotency_key,fingerprint,subject_id,
                          goal_revision,workspace_stop_revision,goal_stop_revision,
                          request,expires_at,traceparent)
                   VALUES(%s,%s,2,'fixture-key',%s,%s,1,1,1,%s,
                          clock_timestamp()+interval '1 minute',
                          '00-11111111111111111111111111111111-2222222222222222-01')""",
                (
                    fixture_permit_id,
                    fixture_claim_id,
                    "2" * 64,
                    subject_id,
                    Jsonb(
                        {
                            "context_id": str(cycle["context_id"]),
                            "operation_id": str(cycle["operation_id"]),
                            "effect": "fixture.noop",
                            "purpose": "fixture_execution",
                            "account_ref": "fixture-account",
                        }
                    ),
                ),
            )
            connection.execute(
                """UPDATE v4_permit_claims
                   SET current_permit_id=%s,revision=2 WHERE id=%s""",
                (fixture_permit_id, fixture_claim_id),
            )
            connection.execute(
                """INSERT INTO v4_stop_scopes(workspace_id,scope_key,revision)
                   VALUES(%s,'workspace',3)""",
                (workspace_id,),
            )
            before_rows = connection.execute(
                """SELECT count(*) FROM v4_production_effect_permits
                   UNION ALL SELECT count(*) FROM v4_production_effect_permit_claims
                   UNION ALL SELECT count(*) FROM v4_production_current_dispatch
                   UNION ALL SELECT count(*) FROM v4_cycles
                   UNION ALL SELECT count(*) FROM v4_run_contexts
                   UNION ALL SELECT count(*) FROM v4_cycle_events"""
            ).fetchall()
            inventory = connection.execute(
                "SELECT public.v4_production_policy_rollback_inventory_v1(%s)",
                (workspace_id,),
            ).fetchone()[0]
            assert (
                inventory["schema_version"]
                == "salience.p1-production-policy-rollback-inventory.v1"
            )
            assert inventory["workspace_id"] == str(workspace_id)
            entries = inventory["inventory"]
            assert entries == sorted(
                entries,
                key=lambda row: (row["source"], row["relation"], row["identity"]),
            )
            relations = {row["relation"] for row in entries}
            current_v4_relations = {
                row[0]
                for row in connection.execute(
                    """SELECT relation.relname
                       FROM pg_class relation
                       JOIN pg_namespace schema ON schema.oid=relation.relnamespace
                       WHERE schema.nspname='public' AND relation.relkind IN ('r','p')
                         AND left(relation.relname,3)='v4_'"""
                ).fetchall()
            }
            assert current_v4_relations <= relations, sorted(
                current_v4_relations - relations
            )
            assert {
                "v4_dispatch_permits",
                "v4_permit_claims",
                "v4_production_effect_permits",
                "v4_production_effect_permit_claims",
                "v4_production_current_dispatch",
                "v4_cycles",
                "v4_run_contexts",
                "v4_cycle_events",
                "v4_cycle_outbox",
                "v4_cycle_inbox",
                "v4_cycle_allocations",
                "v4_allocation_reservations",
                "v4_cost_operations",
                "v4_cost_commands",
                "v4_legacy_dispatches",
                "jobs",
                "publication_requests",
                "publication_plans",
                "publication_attempts",
                "external_effects",
                "budget_reservations",
                "remote_publication_receipts",
                "v4_recovery_cases",
                "v4_runtime_holds",
                "v4_runtime_waits",
                "v4_baseline_revocations",
                "policy_versions",
                "permission_grants",
                "approval_requests",
                "v4_stop_scopes",
            } <= relations
            indexed_rows = {
                (row["relation"], row["source"]): row["row"]
                for row in entries
                if row.get("row") is not None
            }
            assert indexed_rows[("v4_production_effect_permits", "production")][
                "permit_id"
            ] == str(production_permit_id)
            assert indexed_rows[
                ("v4_production_effect_permit_claims", "production")
            ]["claim_id"] == str(production_claim_id)
            assert indexed_rows[
                ("v4_production_current_dispatch", "production")
            ]["current_claim_id"] == str(production_claim_id)
            fixture_permit_rows = [
                row["row"]["id"]
                for row in entries
                if row["relation"] == "v4_dispatch_permits"
                and row["source"] == "fixture"
                and row.get("row") is not None
            ]
            assert set(fixture_permit_rows) == {
                str(fixture_previous_permit_id),
                str(fixture_permit_id),
            }
            assert indexed_rows[("v4_permit_claims", "fixture")]["id"] == str(
                fixture_claim_id
            )
            blocker_relations = {
                row["relation"]
                for row in entries
                if row["source"] == "unavailable"
            }
            assert "v4_recovery_work_items" in blocker_relations
            assert "route_fence_authority" in blocker_relations
            assert "typed_no_effect_recovery_command" in blocker_relations
            unavailable = {
                row["relation"]: row
                for row in entries
                if row["source"] == "unavailable"
            }
            assert unavailable["v4_recovery_work_items"]["inventory_status"] == (
                "future-recovery-family-absent"
            )
            assert unavailable["typed_no_effect_recovery_command"][
                "inventory_status"
            ] == "generation-advance-blocked"
            assert connection.execute(
                "SELECT to_regclass('public.v4_recovery_work_items') IS NULL"
            ).fetchone()[0]
            assert len(entries) == len(
                {
                    (row["source"], row["relation"], row["identity"])
                    for row in entries
                }
            )
            actual_digest = connection.execute(
                "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex')",
                (Jsonb(entries),),
            ).fetchone()[0]
            assert inventory["inventory_sha256"] == actual_digest
            function_contract = connection.execute(
                """SELECT function.provolatile, function.prosecdef,
                          EXISTS (
                            SELECT 1 FROM LATERAL aclexplode(
                              COALESCE(function.proacl,
                                       acldefault('f', function.proowner))
                            ) acl
                            WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
                          )
                   FROM pg_proc function WHERE function.oid=
                     'public.v4_production_policy_rollback_inventory_v1(uuid)'::regprocedure"""
            ).fetchone()
            assert function_contract == ("s", True, False)
            after_rows = connection.execute(
                """SELECT count(*) FROM v4_production_effect_permits
                   UNION ALL SELECT count(*) FROM v4_production_effect_permit_claims
                   UNION ALL SELECT count(*) FROM v4_production_current_dispatch
                   UNION ALL SELECT count(*) FROM v4_cycles
                   UNION ALL SELECT count(*) FROM v4_run_contexts
                   UNION ALL SELECT count(*) FROM v4_cycle_events"""
            ).fetchall()
            assert after_rows == before_rows
    finally:
        _drop_migration_database(source, name)


def test_0044_populated_downgrade_refuses_without_mutation():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _ = _prepare_0044_database("p1_0044_populated_downgrade_")
    try:
        with psycopg.connect(database) as connection:
            workspace_id, subject_id = _seed_0044_workspace_subject(connection)
            _seed_0044_policy(connection, workspace_id)
            connection.execute(
                """INSERT INTO permission_grants(
                          workspace_id,principal_type,principal_id,scope,effect)
                   VALUES(%s,'identity',%s,'production:publish','allow')""",
                (workspace_id, str(subject_id)),
            )
            connection.execute(
                "INSERT INTO v4_stop_scopes(workspace_id,scope_key) VALUES(%s,'workspace')",
                (workspace_id,),
            )
            before = connection.execute(
                """SELECT (SELECT revision FROM identity_subjects WHERE id=%s),
                          (SELECT count(*) FROM permission_grants),
                          (SELECT count(*) FROM policy_versions),
                          (SELECT count(*) FROM v4_stop_scopes),
                          (SELECT count(*) FROM v4_production_effect_permits)""",
                (subject_id,),
            ).fetchone()
        downgrade = _migration(database, "downgrade", _REVISION_0043)
        assert downgrade.returncode != 0
        assert "preserve production policy and revocation authority history" in (
            downgrade.stdout + downgrade.stderr
        )
        with psycopg.connect(database) as connection:
            after = connection.execute(
                """SELECT (SELECT revision FROM identity_subjects WHERE id=%s),
                          (SELECT count(*) FROM permission_grants),
                          (SELECT count(*) FROM policy_versions),
                          (SELECT count(*) FROM v4_stop_scopes),
                          (SELECT count(*) FROM v4_production_effect_permits)""",
                (subject_id,),
            ).fetchone()
            assert after == before
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0044
            assert connection.execute(
                "SELECT to_regclass('public.v4_production_effect_permits') IS NOT NULL"
            ).fetchone()[0]
    finally:
        _drop_migration_database(source, name)


def test_0044_empty_downgrade_preserves_0043_fixture_contract():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, before = _prepare_0044_database("p1_0044_empty_downgrade_")
    try:
        downgraded = _migration(database, "downgrade", _REVISION_0043)
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0043
            _assert_0043_fixture_contract(connection, before)
            assert connection.execute(
                "SELECT to_regclass('public.v4_production_effect_permits')"
            ).fetchone()[0] is None
        upgraded = _migration(database, "upgrade", _REVISION_0044)
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0044
            _assert_0043_fixture_contract(connection, before)
    finally:
        _drop_migration_database(source, name)
