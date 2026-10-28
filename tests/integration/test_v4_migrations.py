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
_REVISION_0045 = "0045_production_policy_approval_guard_repair"
_REVISION_0046 = "0046_production_policy_approval_id_immutability"
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
    assert _REVISION_0046 in resolved_heads.stdout
    assert "(head)" in resolved_heads.stdout

    compose = yaml.safe_load((_MIGRATION_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    assert compose["services"]["migrate"]["command"] == [
        "alembic",
        "-c",
        runtime_config,
        "upgrade",
        "head",
    ]


def test_installed_image_migrates_disposable_database_to_0046(task5_worker_image):
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
            ).fetchall() == [(_REVISION_0046,)]
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

            def preserved(result, older_guard):
                return result.returncode != 0 and any(
                    message in result.stderr
                    for message in (
                        older_guard,
                        "preserve production policy and revocation authority history",
                    )
                )

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
            assert preserved(result, "preserve object identity")
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM object_inventory").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_goals (id,workspace_id,state) VALUES (%s,%s,'active')", (uuid4(),workspace))
            result = migrate("downgrade", "0015_identity_lock")
            assert preserved(result, "preserve V4 cycle guards")
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
            assert preserved(result, "preserve goal revision command history")
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_commands").fetchone()[0]==1
                assert connection.execute("SELECT revision FROM v4_goals WHERE id=%s",(goal,)).fetchone()[0]==2
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]==expected_revision
                connection.execute("INSERT INTO v4_goal_baselines (approval_id,goal_id,goal_revision,subject_id,bundle,expires_at,reason,traceparent) VALUES (%s,%s,2,%s,'{}',now()+interval '1 hour','preservation','fixture')",(uuid4(),goal,subject))
            result=migrate("downgrade","0019_goal_revision_commands")
            assert preserved(result, "preserve baseline approval and revocation history")
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_baselines").fetchone()[0]==1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]==expected_revision
                connection.execute("INSERT INTO v4_goal_revisions (goal_id,revision,schema_version,payload) VALUES (%s,3,'GoalSpec.local.v2','{\"schema_version\":\"GoalSpec.local.v2\"}')", (goal,))
            result = migrate("downgrade", "0020_goal_baselines")
            assert preserved(result, "preserve V2 policy and context history")
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_revisions WHERE revision=3").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_goal_revisions (goal_id,revision,schema_version,payload) VALUES (%s,4,'GoalSpec.local.v3','{\"schema_version\":\"GoalSpec.local.v3\"}')", (goal,))
            result = migrate("downgrade", "0022_program_policy_lock")
            assert preserved(result, "preserve V3")
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_goal_revisions WHERE revision=4").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_stop_scopes (workspace_id,scope_key,stopped) VALUES (%s,'workspace',true)", (workspace,))
            result = migrate("downgrade", "0025_accounting_categories")
            assert preserved(result, "preserve V4 stop, permit and case history")
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT stopped FROM v4_stop_scopes WHERE workspace_id=%s", (workspace,)).fetchone()[0] is True
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_cycle_events (id,workspace_id,subject_id,goal_id,kind,traceparent,payload) VALUES (%s,%s,%s,%s,'outbox_dead_letter',%s,%s)", (uuid4(),workspace,subject,goal,"00-"+"a"*32+"-"+"b"*16+"-01",'{"message_id":"fixture"}'))
            result = migrate("downgrade", "0026_cycle_governance")
            assert preserved(result, "preserve V4 outbox dead-letter escalation history")
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE kind='outbox_dead_letter'").fetchone()[0] == 1
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                connection.execute("INSERT INTO v4_cycle_events (id,workspace_id,subject_id,goal_id,kind,traceparent,payload) VALUES (%s,%s,%s,%s,'fixture_adapter_accepted',%s,%s)", (uuid4(),workspace,subject,goal,"00-"+"a"*32+"-"+"b"*16+"-01",'{"message_id":"fixture"}'))
            result = migrate("downgrade", "0027_auto_dispatch_escalation")
            assert preserved(result, "preserve V4 fixture adapter acceptance history")
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
            assert preserved(result, "preserve exact V4 goal creation receipts")
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
            assert preserved(result, "preserve V4 schedule cutover history")
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
        "account_id": str(uuid4()),
        "provider_id": "production-provider-1",
        "required_scopes": ["effects:write"],
        "budget_id": str(uuid4()),
        "budget_reservation_id": str(uuid4()),
        "policy_version_id": str(policy_id),
        "policy_document_hash": "b" * 64,
        "expires_at": "2099-01-01T00:00:00Z",
    }


def _insert_0044_approval(
    connection,
    workspace_id,
    policy_id,
    context,
    *,
    status="pending",
    decided_by=None,
    decided_at=None,
):
    approval_id = uuid4()
    connection.execute(
        """INSERT INTO approval_requests(
                  id,workspace_id,policy_version_id,effect_type,status,
                  request_context,requested_by,decided_by,decided_at)
           VALUES(%s,%s,%s,'publication',%s,%s,'policy-owner',%s,%s)""",
        (
            approval_id,
            workspace_id,
            policy_id,
            status,
            Jsonb(context),
            decided_by,
            decided_at,
        ),
    )
    return approval_id


def _postgres_statement_rejected(connection, error_pattern, statement):
    class RollbackProbe(Exception):
        pass

    try:
        with connection.transaction():
            statement()
            raise RollbackProbe()
    except RollbackProbe:
        return False
    except psycopg.Error as error:
        assert re.search(error_pattern, str(error)), str(error)
        return True


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
            for invalid_scopes in ([], ["effects:write", "effects:write"], [""]):
                invalid_scope_context = {
                    **context,
                    "required_scopes": invalid_scopes,
                }
                assert _postgres_statement_rejected(
                    connection,
                    "tagged effect approval requires a nonempty unique scope array",
                    lambda invalid_scope_context=invalid_scope_context: _insert_0044_approval(
                        connection,
                        workspace_id,
                        approval_policy_id,
                        invalid_scope_context,
                    ),
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

            untagged_approved_id = connection.execute(
                """INSERT INTO approval_requests(
                          workspace_id,policy_version_id,effect_type,status,
                          request_context,requested_by,decided_by,decided_at)
                   VALUES(%s,%s,'publication','approved','{}'::jsonb,
                          'legacy-owner','legacy-reviewer',clock_timestamp())
                   RETURNING id""",
                (workspace_id, approval_policy_id),
            ).fetchone()[0]
            missing_approval_guards = []
            if not _postgres_statement_rejected(
                connection,
                "tagged effect approval cannot be added to an existing row",
                lambda: connection.execute(
                    "UPDATE approval_requests SET request_context=%s WHERE id=%s",
                    (Jsonb(context), untagged_approved_id),
                ),
            ):
                missing_approval_guards.append("retroactive tagging of an approved row")
            if not _postgres_statement_rejected(
                connection,
                "tagged effect approval must be inserted pending and undecided",
                lambda: _insert_0044_approval(
                    connection,
                    workspace_id,
                    approval_policy_id,
                    context,
                    status="approved",
                    decided_by="reviewer",
                    decided_at=datetime.now(timezone.utc),
                ),
            ):
                missing_approval_guards.append("preapproved tagged insert")
            assert not missing_approval_guards, (
                "missing tagged approval guards: " + ", ".join(missing_approval_guards)
            )
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


def test_0045_repairs_stale_approval_guard_without_weakening_v1_binding():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _ = _prepare_0044_database("p1_0045_approval_guard_repair_")
    try:
        with psycopg.connect(database) as connection:
            workspace_id, subject_id = _seed_0044_workspace_subject(connection)
            cycle = _seed_0044_cycle(connection, workspace_id, subject_id)
            policy_id = _seed_0044_policy(connection, workspace_id, status="active")
            connection.execute(
                """CREATE OR REPLACE FUNCTION public.v4_guard_effect_approval_transition_v1()
                RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
                SET search_path = pg_catalog, public AS $$
                DECLARE
                    tagged boolean;
                BEGIN
                    tagged := NEW.request_context->>'schema_version' =
                              'salience.p1-effect-approval.v1';
                    IF NOT tagged THEN
                        RETURN NEW;
                    END IF;
                    IF jsonb_typeof(NEW.request_context) IS DISTINCT FROM 'object'
                       OR NOT (NEW.request_context ?& ARRAY['schema_version']::text[])
                       OR (NEW.request_context - ARRAY['schema_version']::text[])
                           <> '{}'::jsonb THEN
                        RAISE EXCEPTION
                            'tagged effect approval requires the exact v1 binding fields';
                    END IF;
                    RETURN NEW;
                END
                $$"""
            )
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0044
            assert _postgres_statement_rejected(
                connection,
                "tagged effect approval requires the exact v1 binding fields",
                lambda: connection.execute(
                    """INSERT INTO approval_requests(
                              workspace_id,effect_type,status,request_context,requested_by)
                       VALUES(%s,'creative_distribution','approved',%s,'legacy-fixture')""",
                    (
                        workspace_id,
                        Jsonb({"distribution_package_id": str(uuid4())}),
                    ),
                ),
            )

        upgraded = _migration(database, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

        with psycopg.connect(database) as connection:
            legacy_id = connection.execute(
                """INSERT INTO approval_requests(
                          workspace_id,effect_type,status,request_context,requested_by)
                   VALUES(%s,'creative_distribution','approved',%s,'legacy-fixture')
                   RETURNING id""",
                (
                    workspace_id,
                    Jsonb({"distribution_package_id": str(uuid4())}),
                ),
            ).fetchone()[0]
            assert legacy_id is not None
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0046

            valid_context = _approval_context_0044(
                workspace_id, subject_id, cycle, policy_id
            )
            valid_id = _insert_0044_approval(
                connection, workspace_id, policy_id, valid_context
            )
            assert valid_id is not None

            incomplete_scope_context = dict(valid_context)
            incomplete_scope_context.pop("required_scopes")
            incomplete_budget_context = dict(valid_context)
            incomplete_budget_context.pop("budget_id")
            invalid_account_context = {
                **valid_context,
                "account_id": "fixture-account",
            }
            other_policy_id = _seed_0044_policy(
                connection, workspace_id, status="active", version="2"
            )
            mismatched_policy_context = {
                **valid_context,
                "policy_version_id": str(other_policy_id),
            }
            invalid_operation_context = {
                **valid_context,
                "operation_id": "not-a-uuid",
            }
            invalid_budget_context = {
                **valid_context,
                "budget_reservation_id": "not-a-uuid",
            }
            for context, message in (
                (
                    incomplete_scope_context,
                    "tagged effect approval requires the exact v1 binding fields",
                ),
                (
                    incomplete_budget_context,
                    "tagged effect approval requires the exact v1 binding fields",
                ),
                (
                    invalid_account_context,
                    "tagged effect approval binding does not match its row",
                ),
                (
                    mismatched_policy_context,
                    "tagged effect approval binding does not match its row",
                ),
                (
                    invalid_operation_context,
                    "tagged effect approval contains an invalid operation_id binding",
                ),
                (
                    invalid_budget_context,
                    "tagged effect approval contains an invalid budget_reservation_id binding",
                ),
            ):
                assert _postgres_statement_rejected(
                    connection,
                    message,
                    lambda context=context: _insert_0044_approval(
                        connection, workspace_id, policy_id, context
                    ),
                )

        downgraded = _migration(database, "downgrade", _REVISION_0044)
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0044
            assert connection.execute(
                "SELECT count(*) FROM approval_requests WHERE id = ANY(%s)",
                ([legacy_id, valid_id],),
            ).fetchone()[0] == 2
            assert _postgres_statement_rejected(
                connection,
                "tagged effect approval requires the exact v1 binding fields",
                lambda: _insert_0044_approval(
                    connection,
                    workspace_id,
                    policy_id,
                    {key: value for key, value in valid_context.items()
                     if key != "required_scopes"},
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0046_freezes_decided_tagged_approval_identity_from_stamped_0045():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _ = _prepare_0044_database("p1_0046_approval_id_immutability_")
    try:
        upgraded_0045 = _migration(database, "upgrade", _REVISION_0045)
        assert upgraded_0045.returncode == 0, upgraded_0045.stdout + upgraded_0045.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0045
            workspace_id, subject_id = _seed_0044_workspace_subject(connection)
            cycle = _seed_0044_cycle(connection, workspace_id, subject_id)
            policy_id = _seed_0044_policy(connection, workspace_id, status="active")
            context = _approval_context_0044(workspace_id, subject_id, cycle, policy_id)

            approved_id = _insert_0044_approval(
                connection, workspace_id, policy_id, context
            )
            connection.execute(
                """UPDATE approval_requests SET status='approved',decided_by='reviewer',
                          decided_at=clock_timestamp() WHERE id=%s""",
                (approved_id,),
            )

            revoked_id = _insert_0044_approval(
                connection, workspace_id, policy_id, context
            )
            connection.execute(
                """UPDATE approval_requests SET status='approved',decided_by='reviewer',
                          decided_at=clock_timestamp() WHERE id=%s""",
                (revoked_id,),
            )
            with connection.transaction():
                transaction_id = str(
                    connection.execute("SELECT txid_current()").fetchone()[0]
                )
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
                        Jsonb(
                            {
                                "schema_version": "salience.p1-effect-approval-revocation.v1",
                                "approval_request_id": str(revoked_id),
                                "operation_id": str(cycle["operation_id"]),
                                "policy_version_id": str(policy_id),
                                "transaction_id": transaction_id,
                            }
                        ),
                    ),
                )
                connection.execute(
                    "UPDATE approval_requests SET status='revoked' WHERE id=%s",
                    (revoked_id,),
                )

            unrelated_id = connection.execute(
                """INSERT INTO approval_requests(
                          workspace_id,policy_version_id,effect_type,status,
                          request_context,requested_by)
                   VALUES(%s,%s,'legacy','approved','{}'::jsonb,'legacy-owner')
                   RETURNING id""",
                (workspace_id, policy_id),
            ).fetchone()[0]

        upgraded_head = _migration(database, "upgrade", "head")
        assert upgraded_head.returncode == 0, upgraded_head.stdout + upgraded_head.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0046
            tagged_ids = [approved_id, revoked_id]
            assert connection.execute(
                """SELECT count(*) FROM v4_production_effect_permits
                   WHERE approval_request_id = ANY(%s)""",
                (tagged_ids,),
            ).fetchone()[0] == 0

            for approval_id in tagged_ids:
                replacement_id = uuid4()
                assert _postgres_statement_rejected(
                    connection,
                    "decided effect approval identity is immutable",
                    lambda: connection.execute(
                        "UPDATE approval_requests SET id=%s WHERE id=%s",
                        (replacement_id, approval_id),
                    ),
                )
                assert _postgres_statement_rejected(
                    connection,
                    "tagged effect approval decision history cannot be deleted",
                    lambda approval_id=approval_id: connection.execute(
                        "DELETE FROM approval_requests WHERE id=%s", (approval_id,)
                    ),
                )

            replacement_legacy_id = uuid4()
            connection.execute(
                "UPDATE approval_requests SET id=%s WHERE id=%s",
                (replacement_legacy_id, unrelated_id),
            )
            connection.execute(
                "DELETE FROM approval_requests WHERE id=%s", (replacement_legacy_id,)
            )
            identity_guard = connection.execute(
                """SELECT function.prosecdef,function.proconfig,function.proacl
                   FROM pg_proc function
                   WHERE function.oid =
                     'public.v4_guard_effect_approval_identity_v1()'::regprocedure"""
            ).fetchone()
            assert identity_guard is not None
            assert identity_guard[0] is True
            assert "search_path=pg_catalog, public" in identity_guard[1]
            assert not any(entry.startswith("=X/") for entry in (identity_guard[2] or []))

        downgraded = _migration(database, "downgrade", _REVISION_0045)
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0045
            assert connection.execute(
                "SELECT count(*) FROM approval_requests WHERE id = ANY(%s)",
                ([approved_id, revoked_id],),
            ).fetchone()[0] == 2
            assert _postgres_statement_rejected(
                connection,
                "decided effect approval identity is immutable",
                lambda: connection.execute(
                    "UPDATE approval_requests SET id=%s WHERE id=%s",
                    (uuid4(), approved_id),
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "tagged effect approval decision history cannot be deleted",
                lambda: connection.execute(
                    "DELETE FROM approval_requests WHERE id=%s", (revoked_id,)
                ),
            )
            incomplete_context = dict(context)
            incomplete_context.pop("required_scopes")
            assert _postgres_statement_rejected(
                connection,
                "tagged effect approval requires the exact v1 binding fields",
                lambda: _insert_0044_approval(
                    connection, workspace_id, policy_id, incomplete_context
                ),
            )
    finally:
        _drop_migration_database(source, name)


def _insert_0044_production_permit(
    connection,
    binding,
    *,
    generation=1,
    issued_offset="0 seconds",
    expires_offset="30 seconds",
):
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
        "approval_expires_at": binding.get(
            "approval_expires_at", "2099-01-01T00:00:00+00:00"
        ),
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
                  %(route_fence_decision_id)s,
                  clock_timestamp()+%(issued_offset)s::interval,
                  clock_timestamp()+%(expires_offset)s::interval,
                  %(request_snapshot)s)""",
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
            "issued_offset": issued_offset,
            "expires_offset": expires_offset,
            "artifact_hash": "c" * 64,
            **route,
            "request_snapshot": Jsonb(request),
        },
    )
    return permit_id, idempotency_key


def test_0044_production_claim_is_unique_and_mode_separated():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _ = _prepare_0044_database("p1_0044_production_claim_")
    pointer_writer_role = "p1_0044_pointer_writer_" + uuid4().hex[:12]
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
            missing_permit_guards = []
            if not _postgres_statement_rejected(
                connection,
                "production permit expiry must not exceed 60 seconds",
                lambda: _insert_0044_production_permit(
                    connection,
                    binding,
                    generation=2,
                    expires_offset="61 seconds",
                ),
            ):
                missing_permit_guards.append("permit expiry longer than 60 seconds")

            future_binding = {**binding, "operation_id": uuid4()}
            future_attempt = {}

            def claim_future_permit():
                future_permit_id, future_idempotency_key = (
                    _insert_0044_production_permit(
                        connection,
                        future_binding,
                        issued_offset="61 seconds",
                        expires_offset="91 seconds",
                    )
                )
                future_claim_id = uuid4()
                connection.execute(
                    """INSERT INTO v4_production_effect_permit_claims(
                              claim_id,permit_id,operation_id,generation,
                              route_fence_decision_id)
                       SELECT %s,permit_id,operation_id,generation,route_fence_decision_id
                       FROM v4_production_effect_permits WHERE permit_id=%s""",
                    (future_claim_id, future_permit_id),
                )
                connection.execute(
                    """SELECT public.v4_advance_production_current_dispatch_v1(
                              %s,0,%s,%s,%s,NULL)""",
                    (
                        future_binding["operation_id"],
                        future_permit_id,
                        future_claim_id,
                        future_idempotency_key,
                    ),
                )
                future_attempt["pointer"] = connection.execute(
                    """SELECT generation,current_permit_id,current_claim_id
                       FROM v4_production_current_dispatch WHERE operation_id=%s""",
                    (future_binding["operation_id"],),
                ).fetchone()

            if not _postgres_statement_rejected(
                connection,
                "production permit issued_at cannot be in the future",
                claim_future_permit,
            ):
                missing_permit_guards.append("future-dated permit could be claimed")
            assert "pointer" not in future_attempt, (
                "future-dated production permit became claimable: "
                f"{future_attempt.get('pointer')}"
            )
            assert connection.execute(
                "SELECT count(*) FROM v4_production_effect_permits WHERE operation_id=%s",
                (future_binding["operation_id"],),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_effect_permit_claims WHERE operation_id=%s",
                (future_binding["operation_id"],),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_current_dispatch WHERE operation_id=%s",
                (future_binding["operation_id"],),
            ).fetchone()[0] == 0

            near_expiry = connection.execute(
                "SELECT clock_timestamp()+interval '10 seconds'"
            ).fetchone()[0]
            near_expiry_context = {
                **approval_context,
                "expires_at": near_expiry.isoformat(),
            }
            near_expiry_approval_id = _insert_0044_approval(
                connection,
                workspace_id,
                policy_id,
                near_expiry_context,
            )
            connection.execute(
                """UPDATE approval_requests SET status='approved',
                          decided_by='reviewer',decided_at=clock_timestamp()
                   WHERE id=%s""",
                (near_expiry_approval_id,),
            )
            late_permit_binding = {
                **binding,
                "approval_id": near_expiry_approval_id,
                "approval_expires_at": near_expiry.isoformat(),
            }
            if not _postgres_statement_rejected(
                connection,
                "production permit cannot expire after canonical approval",
                lambda: _insert_0044_production_permit(
                    connection,
                    late_permit_binding,
                    generation=2,
                    expires_offset="30 seconds",
                ),
            ):
                missing_permit_guards.append("permit expiry later than approval expiry")

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
                """SELECT public.v4_advance_production_current_dispatch_v1(
                          %s,0,%s,NULL,%s,NULL)""",
                (cycle["operation_id"], permit_id, idempotency_key),
            )
            pointer_without_claim = connection.execute(
                """SELECT generation,current_permit_id,current_claim_id
                   FROM v4_production_current_dispatch WHERE operation_id=%s""",
                (cycle["operation_id"],),
            ).fetchone()
            assert pointer_without_claim == (1, permit_id, None)
            # The claim history arrives after initial issue and fills this pointer.
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
            connection.execute(
                psycopg.sql.SQL("CREATE ROLE {} NOLOGIN").format(
                    psycopg.sql.Identifier(pointer_writer_role)
                )
            )
            connection.execute(
                psycopg.sql.SQL(
                    "GRANT SELECT, UPDATE ON TABLE "
                    "public.v4_production_current_dispatch TO {}"
                ).format(psycopg.sql.Identifier(pointer_writer_role))
            )
            connection.execute(
                psycopg.sql.SQL("SET ROLE {}").format(
                    psycopg.sql.Identifier(pointer_writer_role)
                )
            )
            connection.execute(
                "SELECT set_config('salience.production_current_dispatch_v1', 'advance', true)"
            )
            if not _postgres_statement_rejected(
                connection,
                "production current dispatch writes require the v1 function owner",
                lambda: connection.execute(
                    """UPDATE v4_production_current_dispatch
                       SET idempotency_key='spoofed-guc-write'
                       WHERE operation_id=%s""",
                    (cycle["operation_id"],),
                ),
            ):
                missing_permit_guards.append("GUC spoof by non-owner DML role")
            connection.execute("RESET ROLE")
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

            expired_binding = {**binding, "operation_id": uuid4()}
            expired_permit_id, expired_idempotency_key = (
                _insert_0044_production_permit(
                    connection,
                    expired_binding,
                    expires_offset="1 second",
                )
            )
            expired_claim_id = uuid4()
            connection.execute(
                """INSERT INTO v4_production_effect_permit_claims(
                          claim_id,permit_id,operation_id,generation,
                          route_fence_decision_id)
                   SELECT %s,permit_id,operation_id,generation,route_fence_decision_id
                   FROM v4_production_effect_permits WHERE permit_id=%s""",
                (expired_claim_id, expired_permit_id),
            )
            time.sleep(1.1)
            if not _postgres_statement_rejected(
                connection,
                "production permit has expired",
                lambda: connection.execute(
                    """SELECT public.v4_advance_production_current_dispatch_v1(
                              %s,0,%s,%s,%s,NULL)""",
                    (
                        expired_binding["operation_id"],
                        expired_permit_id,
                        expired_claim_id,
                        expired_idempotency_key,
                    ),
                ),
            ):
                missing_permit_guards.append("claim through an expired permit")
            assert connection.execute(
                """SELECT count(*) FROM v4_production_current_dispatch
                   WHERE operation_id=%s""",
                (expired_binding["operation_id"],),
            ).fetchone()[0] == 0
            assert not missing_permit_guards, (
                "missing production dispatch guards: " + ", ".join(missing_permit_guards)
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
        _drop_migration_database(source, name, role=pointer_writer_role)


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


_REVISION_0047 = "0047_p1_production_recovery_work_events"
_RECOVERY_V1_KEYS = (
    "production_intent_enrollment",
    "goal",
    "intent",
    "cycle",
    "context",
    "job",
    "production_current_dispatch",
    "production_permits",
    "production_permit_claims",
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


def _seed_0047_legacy_history(connection):
    """Seed unmarked fixture-era rows that a production migration must preserve."""
    workspace_id, subject_id = _seed_0044_workspace_subject(connection)
    cycle = _seed_0044_cycle(connection, workspace_id, subject_id)
    case_id = uuid4()
    notification_id = uuid4()
    connection.execute(
        """INSERT INTO v4_recovery_cases(
                  id,workspace_id,goal_id,target_cycle_id,context_id,operation_id,
                  kind,open_fingerprint,failure_class,state,revision,owner_id,reason,
                  deadline,max_actions)
           VALUES(%s,%s,%s,%s,%s,%s,'manual_review',%s,'manual_review',
                  'awaiting_review',1,%s,'fixture history',
                  clock_timestamp()+interval '1 day',3)""",
        (
            case_id,
            workspace_id,
            cycle["goal_id"],
            cycle["cycle_id"],
            cycle["context_id"],
            cycle["operation_id"],
            "f" * 64,
            subject_id,
        ),
    )
    connection.execute(
        """INSERT INTO v4_case_notifications(
                  id,case_id,case_revision,kind,due_at)
           VALUES(%s,%s,1,'review_due',clock_timestamp()+interval '1 hour')""",
        (notification_id, case_id),
    )

    fixture_hold_id = uuid4()
    fixture_operation_id = uuid4()
    fixture_snapshot = Jsonb({key: None for key in _V1_KEYS})
    fixture_snapshot_hash = connection.execute(
        "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex')",
        (fixture_snapshot,),
    ).fetchone()[0]
    connection.execute("SET session_replication_role = replica")
    connection.execute(
        """INSERT INTO v4_legacy_publication_recovery_holds(
                  id,operation_id,workspace_id,goal_id,intent_id,cycle_id,context_id,
                  job_id,publication_attempt_id,permit_id,owner_id,execution_deadline,
                  permit_expires_at,request_fingerprint,receipt_fingerprint,
                  liability_fingerprint,remote_id,remote_state,reason,state,
                  liability_snapshot,liability_snapshot_version,liability_snapshot_v1,
                  liability_fingerprint_v1)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  clock_timestamp()+interval '1 day',clock_timestamp()+interval '1 hour',
                  %s,%s,%s,%s,'accepted','expired_permit_receipt','held',%s,
                  'salience.publication-liability.v1',%s,%s)""",
        (
            fixture_hold_id,
            fixture_operation_id,
            workspace_id,
            cycle["goal_id"],
            cycle["intent_id"],
            cycle["cycle_id"],
            cycle["context_id"],
            uuid4(),
            uuid4(),
            uuid4(),
            subject_id,
            "a" * 64,
            "b" * 64,
            fixture_snapshot_hash,
            "fixture-v4-publication:" + str(fixture_operation_id),
            Jsonb({"fixture_snapshot": True}),
            fixture_snapshot,
            fixture_snapshot_hash,
        ),
    )
    connection.execute("SET session_replication_role = origin")
    return {
        "workspace_id": workspace_id,
        "subject_id": subject_id,
        "cycle": cycle,
        "case_id": case_id,
        "notification_id": notification_id,
        "fixture_hold_id": fixture_hold_id,
        "fixture_operation_id": fixture_operation_id,
    }


def _seed_0047_slice2_authority(connection):
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
    }
    permit_id, idempotency_key = _insert_0044_production_permit(
        connection,
        binding,
    )
    claim_id = uuid4()
    connection.execute(
        """INSERT INTO v4_production_effect_permit_claims(
                  claim_id,permit_id,operation_id,generation,route_fence_decision_id)
           SELECT %s,permit_id,operation_id,generation,route_fence_decision_id
           FROM v4_production_effect_permits WHERE permit_id=%s""",
        (claim_id, permit_id),
    )
    connection.execute(
        """SELECT public.v4_advance_production_current_dispatch_v1(
                  %s,0,%s,%s,%s,NULL)""",
        (cycle["operation_id"], permit_id, claim_id, idempotency_key),
    )
    # Create the historical expired-permit condition only after the 0044
    # dispatch guard has accepted and recorded the permit as current.
    connection.execute("SET session_replication_role = replica")
    connection.execute(
        """UPDATE v4_production_effect_permits
           SET issued_at=clock_timestamp()-interval '120 seconds',
               expires_at=clock_timestamp()-interval '61 seconds'
           WHERE permit_id=%s""",
        (permit_id,),
    )
    connection.execute("SET session_replication_role = origin")
    assert connection.execute(
        """SELECT EXISTS (
               SELECT 1
               FROM v4_production_current_dispatch dispatch
               JOIN v4_production_effect_permits permit
                 ON permit.permit_id=dispatch.current_permit_id
                AND permit.operation_id=dispatch.operation_id
                AND permit.generation=dispatch.generation
               JOIN v4_production_effect_permit_claims claim
                 ON claim.claim_id=dispatch.current_claim_id
                AND claim.permit_id=permit.permit_id
                AND claim.operation_id=permit.operation_id
                AND claim.generation=permit.generation
               WHERE dispatch.operation_id=%s AND dispatch.generation=1
                 AND permit.intent_id=%s AND permit.cycle_id=%s
                 AND permit.context_id=%s
           )""",
        (
            cycle["operation_id"],
            cycle["intent_id"],
            cycle["cycle_id"],
            cycle["context_id"],
        ),
    ).fetchone()[0]
    grant_id = connection.execute(
        """INSERT INTO permission_grants(
                  workspace_id,principal_type,principal_id,scope,effect,
                  constraints,expires_at)
           VALUES(%s,'identity',%s,'production:recovery','allow','{}',
                  clock_timestamp()+interval '1 day') RETURNING id""",
        (workspace_id, str(subject_id)),
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO v4_stop_scopes(
                  workspace_id,scope_key,revision,stopped,changed_by,reason)
           VALUES(%s,'workspace',1,false,%s,'not stopped'),
                 (%s,%s,1,false,%s,'not stopped')""",
        (
            workspace_id,
            subject_id,
            workspace_id,
            "goal:" + str(cycle["goal_id"]),
            subject_id,
        ),
    )
    subject_revision = connection.execute(
        "SELECT revision FROM identity_subjects WHERE id=%s", (subject_id,)
    ).fetchone()[0]
    return {
        **binding,
        "permit_id": permit_id,
        "claim_id": claim_id,
        "grant_id": grant_id,
        "subject_revision": subject_revision,
        "idempotency_key": idempotency_key,
    }


def _prepare_0047_0046_database(prefix):
    source = os.environ["TEST_DATABASE_URL"]
    fixture_state = {}

    def seed_legacy(connection):
        fixture_state.update(_seed_0047_legacy_history(connection))

    name, database, _ = _prepare_0044_database(prefix, seed=seed_legacy)
    try:
        upgraded_0045 = _migration(database, "upgrade", _REVISION_0045)
        assert upgraded_0045.returncode == 0, upgraded_0045.stdout + upgraded_0045.stderr
        upgraded_0046 = _migration(database, "upgrade", _REVISION_0046)
        assert upgraded_0046.returncode == 0, upgraded_0046.stdout + upgraded_0046.stderr
        with psycopg.connect(database) as connection:
            authority = _seed_0047_slice2_authority(connection)
            before_0046 = _capture_0043_fixture_contract(connection)
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0046
            for relation in (
                "v4_production_intent_enrollments",
                "v4_production_recovery_work",
                "v4_production_recovery_events",
            ):
                assert connection.execute(
                    "SELECT to_regclass(%s)", ("public." + relation,)
                ).fetchone()[0] is None
        return name, database, before_0046, fixture_state, authority
    except Exception:
        _drop_migration_database(source, name)
        raise


def _upgrade_0047_or_fail(database):
    upgraded = _migration(database, "upgrade", _REVISION_0047)
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    return upgraded


def _insert_0047_enrollment(connection, authority, *, intent_id=None, overrides=None):
    intent_id = intent_id or authority["cycle"]["intent_id"]
    intent = connection.execute(
        """SELECT goal_id,goal_revision,eligibility_revision
           FROM v4_cycle_intents WHERE id=%s""",
        (intent_id,),
    ).fetchone()
    assert intent is not None
    row = {
        "intent_id": intent_id,
        "eligibility_revision": intent[2],
        "workspace_id": authority["workspace_id"],
        "goal_id": intent[0],
        "goal_revision": intent[1],
        "source_owner_id": authority["subject_id"],
        "subject_id": authority["subject_id"],
        "subject_revision": authority["subject_revision"],
        "grant_ids": Jsonb([str(authority["grant_id"])]),
        "policy_version_id": authority["policy_id"],
        "policy_document_hash": "b" * 64,
        "workspace_stop_revision": 1,
        "goal_stop_revision": 1,
        "artifact_sha256": "c" * 64,
        "route_id": uuid4(),
        "slot_id": uuid4(),
        "route_owner_id": authority["subject_id"],
        "fencing_epoch": 7,
        "route_manifest_sha256": "d" * 64,
        "external_lock_sha256": "e" * 64,
        "route_fence_decision_id": uuid4(),
    }
    # This helper runs as the disposable database owner to exercise the
    # migration's immutable storage/source guards. Its route tuple is fixture
    # input, not proof that a permit or this trigger verified an external
    # fence. Task 6's restricted writer must perform that live check before
    # it is granted the sole application enrollment path.
    if overrides:
        row.update(overrides)
    cursor = connection.execute(
        """INSERT INTO v4_production_intent_enrollments(
                  intent_id,eligibility_revision,workspace_id,goal_id,goal_revision,
                  source_owner_id,subject_id,subject_revision,grant_ids,
                  policy_version_id,policy_document_hash,workspace_stop_revision,
                  goal_stop_revision,artifact_sha256,route_id,slot_id,route_owner_id,
                  fencing_epoch,route_manifest_sha256,external_lock_sha256,
                  route_fence_decision_id)
           VALUES(%(intent_id)s,%(eligibility_revision)s,%(workspace_id)s,%(goal_id)s,
                  %(goal_revision)s,%(source_owner_id)s,%(subject_id)s,
                  %(subject_revision)s,%(grant_ids)s,%(policy_version_id)s,
                  %(policy_document_hash)s,%(workspace_stop_revision)s,
                  %(goal_stop_revision)s,%(artifact_sha256)s,%(route_id)s,%(slot_id)s,
                  %(route_owner_id)s,%(fencing_epoch)s,%(route_manifest_sha256)s,
                  %(external_lock_sha256)s,%(route_fence_decision_id)s)
           RETURNING *""",
        row,
    )
    result = cursor.fetchone()
    return dict(zip((column.name for column in cursor.description), result))


def _insert_0047_recovery_work(
    connection,
    authority,
    *,
    source_discriminant,
    source_id,
    source_revision,
    case_revision=None,
    eligibility_revision=1,
    cycle_id=None,
    context_id=None,
    operation_id=None,
    intent_id=None,
    target_alias=None,
    remote_account_id=None,
    remote_resource_id=None,
    reason=None,
    state=None,
    snapshot=None,
    snapshot_version=None,
    snapshot_sha256=None,
    deadline=None,
    due_at=None,
    expires_at=None,
    request_fingerprint=None,
):
    deadline = deadline or connection.execute(
        "SELECT clock_timestamp()+interval '1 day'"
    ).fetchone()[0]
    if request_fingerprint is None:
        request_fingerprint = "a" * 64
    cursor = connection.execute(
        """INSERT INTO v4_production_recovery_work(
                  work_id,source_discriminant,source_id,workspace_id,goal_id,intent_id,
                  cycle_id,context_id,operation_id,source_revision,case_revision,
                  eligibility_revision,owner_id,target_alias,remote_account_id,
                  remote_resource_id,reason,state,request_fingerprint,
                  snapshot_version,snapshot,snapshot_sha256,deadline,due_at,expires_at,
                  deadline_revision,recipient_profile_revision)
           VALUES(%(work_id)s,%(source_discriminant)s,%(source_id)s,%(workspace_id)s,
                  %(goal_id)s,%(intent_id)s,%(cycle_id)s,%(context_id)s,%(operation_id)s,
                  %(source_revision)s,%(case_revision)s,%(eligibility_revision)s,
                  %(owner_id)s,%(target_alias)s,%(remote_account_id)s,
                  %(remote_resource_id)s,%(reason)s,%(state)s,%(request_fingerprint)s,
                  %(snapshot_version)s,%(snapshot)s,%(snapshot_sha256)s,%(deadline)s,
                  %(due_at)s,%(expires_at)s,1,1) RETURNING *""",
        {
            "work_id": uuid4(),
            "source_discriminant": source_discriminant,
            "source_id": source_id,
            "workspace_id": authority["workspace_id"],
            "goal_id": authority["cycle"]["goal_id"],
            "intent_id": intent_id or authority["cycle"]["intent_id"],
            "cycle_id": cycle_id,
            "context_id": context_id,
            "operation_id": operation_id,
            "source_revision": source_revision,
            "case_revision": case_revision,
            "eligibility_revision": eligibility_revision,
            "owner_id": authority["subject_id"],
            "target_alias": target_alias,
            "remote_account_id": remote_account_id,
            "remote_resource_id": remote_resource_id,
            "reason": reason,
            "state": state,
            "request_fingerprint": request_fingerprint,
            "snapshot_version": snapshot_version,
            "snapshot": Jsonb(snapshot) if snapshot is not None else None,
            "snapshot_sha256": snapshot_sha256,
            "deadline": deadline,
            "due_at": due_at,
            "expires_at": expires_at,
        },
    )
    result = cursor.fetchone()
    return dict(zip((column.name for column in cursor.description), result))


def _production_snapshot_fixture(connection, authority, enrollment):
    snapshot = connection.execute(
        """SELECT public.v4_production_recovery_publication_snapshot_v1(%s)""",
        (authority["operation_id"],),
    ).fetchone()[0]
    assert snapshot is not None
    snapshot_hash = connection.execute(
        "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex')",
        (Jsonb(snapshot),),
    ).fetchone()[0]
    return snapshot, snapshot_hash


def _production_request_fingerprint(connection, authority):
    return connection.execute(
        """SELECT encode(public.digest(request_snapshot::text,'sha256'),'hex')
           FROM v4_production_effect_permits WHERE permit_id=%s""",
        (authority["permit_id"],),
    ).fetchone()[0]


def _capture_0047_new_schema_contract(connection):
    contract = {}
    for table_name in (
        "v4_production_intent_enrollments",
        "v4_production_recovery_work",
        "v4_production_recovery_events",
    ):
        contract[table_name] = {
            "columns": connection.execute(
                """SELECT column_name,data_type,udt_name,is_nullable,column_default,
                          ordinal_position
                   FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=%s
                   ORDER BY ordinal_position""",
                (table_name,),
            ).fetchall(),
            "constraints": connection.execute(
                """SELECT conname,pg_get_constraintdef(oid)
                   FROM pg_constraint WHERE conrelid=%s::regclass
                   ORDER BY conname""",
                ("public." + table_name,),
            ).fetchall(),
            "indexes": connection.execute(
                """SELECT indexname,indexdef FROM pg_indexes
                   WHERE schemaname='public' AND tablename=%s
                   ORDER BY indexname""",
                (table_name,),
            ).fetchall(),
            "triggers": connection.execute(
                """SELECT tgname,pg_get_triggerdef(oid),tgenabled
                   FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal
                   ORDER BY tgname""",
                ("public." + table_name,),
            ).fetchall(),
        }
    contract["functions"] = connection.execute(
        """SELECT proc.proname,pg_get_function_identity_arguments(proc.oid),
                  pg_get_functiondef(proc.oid),proc.prosecdef,proc.proconfig,
                  COALESCE(proc.proacl,acldefault('f',proc.proowner))::text
           FROM pg_proc proc JOIN pg_namespace schema
             ON schema.oid=proc.pronamespace
           WHERE schema.nspname='public' AND proc.proname = ANY(%s)
           ORDER BY proc.proname,pg_get_function_identity_arguments(proc.oid)""",
        (
            [
                "v4_validate_production_intent_enrollment_v1",
                "v4_production_recovery_publication_snapshot_v1",
                "v4_validate_production_recovery_work_v1",
                "v4_validate_production_recovery_event_v1",
                "v4_reject_production_recovery_history_change_v1",
            ],
        ),
    ).fetchall()
    return contract


def test_0047_upgrade_preserves_0046_authority_history():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, before, fixture, authority = _prepare_0047_0046_database(
        "p1_0047_preserve_"
    )
    try:
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0046
            assert connection.execute(
                "SELECT count(*) FROM v4_legacy_publication_recovery_holds WHERE id=%s",
                (fixture["fixture_hold_id"],),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_recovery_cases WHERE id=%s",
                (fixture["case_id"],),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_case_notifications WHERE id=%s",
                (fixture["notification_id"],),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_production_effect_permits WHERE permit_id=%s",
                (authority["permit_id"],),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_production_effect_permit_claims WHERE claim_id=%s",
                (authority["claim_id"],),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_production_current_dispatch WHERE operation_id=%s",
                (authority["operation_id"],),
            ).fetchone()[0] == 1

        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0047
            _assert_0043_fixture_contract(connection, before)
            assert connection.execute(
                "SELECT count(*) FROM v4_production_intent_enrollments"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_work"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events"
            ).fetchone()[0] == 0
    finally:
        _drop_migration_database(source, name)


def test_0047_production_intent_enrollment_requires_complete_current_authority():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0047_0046_database(
        "p1_0047_enrollment_complete_"
    )
    try:
        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            valid = _insert_0047_enrollment(connection, authority)
            assert valid["intent_id"] == authority["cycle"]["intent_id"]
            assert connection.execute(
                """SELECT EXISTS (
                       SELECT 1
                       FROM pg_class relation
                       CROSS JOIN LATERAL aclexplode(
                           COALESCE(relation.relacl,
                                    acldefault('r',relation.relowner))
                       ) privilege
                       WHERE relation.oid =
                             'public.v4_production_intent_enrollments'::regclass
                         AND privilege.grantee = 0
                         AND privilege.privilege_type IN ('INSERT','UPDATE','DELETE')
                   )"""
            ).fetchone()[0] is False
            enrollment_boundary = connection.execute(
                "SELECT obj_description("
                "'public.v4_production_intent_enrollments'::regclass,'pg_class')"
            ).fetchone()[0]
            assert "does not treat a production permit as route-fence verification" in enrollment_boundary
            assert "restricted Task 6 writer must verify the live fence" in enrollment_boundary

            assert _postgres_statement_rejected(
                connection,
                "canonical production grant IDs|required.*grant",
                lambda: _insert_0047_enrollment(
                    connection,
                    authority,
                    overrides={"grant_ids": Jsonb([])},
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "current intent eligibility|production intent enrollment",
                lambda: _insert_0047_enrollment(
                    connection,
                    authority,
                    overrides={"eligibility_revision": 99},
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "current workspace stop|stop revision|production intent enrollment",
                lambda: _insert_0047_enrollment(
                    connection,
                    authority,
                    overrides={"workspace_stop_revision": 99},
                ),
            )

            role = "p1_0047_enroll_ro_" + uuid4().hex[:12]
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
                psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role))
            )
            for denied_write in (
                lambda: connection.execute(
                    "INSERT INTO v4_production_intent_enrollments DEFAULT VALUES"
                ),
                lambda: connection.execute(
                    "UPDATE v4_production_intent_enrollments SET intent_id=intent_id "
                    "WHERE intent_id=%s",
                    (valid["intent_id"],),
                ),
                lambda: connection.execute(
                    "DELETE FROM v4_production_intent_enrollments WHERE intent_id=%s",
                    (valid["intent_id"],),
                ),
            ):
                assert _postgres_statement_rejected(
                    connection, "permission denied", denied_write
                )
            connection.execute("RESET ROLE")
        _drop_migration_database(source, name, role=role)
        name = None
    finally:
        if name is not None:
            _drop_migration_database(source, name)


def test_0047_production_intent_enrollment_is_immutable_and_has_no_backfill():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, fixture, authority = _prepare_0047_0046_database(
        "p1_0047_enrollment_immutable_"
    )
    try:
        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_intent_enrollments"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_work"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events"
            ).fetchone()[0] == 0

            historical_intent_id = fixture["cycle"]["intent_id"]
            assert connection.execute(
                """SELECT count(*) FROM v4_production_intent_enrollments
                   WHERE intent_id=%s""",
                (historical_intent_id,),
            ).fetchone()[0] == 0
            enrollment = _insert_0047_enrollment(connection, authority)
            assert _postgres_statement_rejected(
                connection,
                "production intent enrollment is immutable",
                lambda: connection.execute(
                    "UPDATE v4_production_intent_enrollments SET artifact_sha256=%s "
                    "WHERE intent_id=%s AND eligibility_revision=%s",
                    ("f" * 64, enrollment["intent_id"], enrollment["eligibility_revision"]),
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "production intent enrollment is immutable",
                lambda: connection.execute(
                    "DELETE FROM v4_production_intent_enrollments "
                    "WHERE intent_id=%s AND eligibility_revision=%s",
                    (enrollment["intent_id"], enrollment["eligibility_revision"]),
                ),
            )

            before = connection.execute(
                """SELECT to_jsonb(e) FROM v4_production_intent_enrollments e
                   WHERE intent_id=%s AND eligibility_revision=%s""",
                (enrollment["intent_id"], enrollment["eligibility_revision"]),
            ).fetchone()[0]
            assert before["intent_id"] == str(enrollment["intent_id"])
    finally:
        _drop_migration_database(source, name)


def test_0047_recovery_source_discriminants_and_snapshots_are_guarded():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, fixture, authority = _prepare_0047_0046_database(
        "p1_0047_work_sources_"
    )
    try:
        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            enrollment = _insert_0047_enrollment(connection, authority)
            cycle = authority["cycle"]
            permit_snapshot = connection.execute(
                """SELECT request_snapshot FROM v4_production_effect_permits
                   WHERE permit_id=%s""",
                (authority["permit_id"],),
            ).fetchone()[0]
            request_fingerprint = connection.execute(
                "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex')",
                (Jsonb(permit_snapshot),),
            ).fetchone()[0]

            case_id = uuid4()
            case_revision = 1
            case_deadline = connection.execute(
                "SELECT clock_timestamp()+interval '1 day'"
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO v4_recovery_cases(
                          id,workspace_id,goal_id,target_cycle_id,context_id,operation_id,
                          kind,open_fingerprint,failure_class,state,revision,owner_id,
                          reason,deadline,max_actions)
                   VALUES(%s,%s,%s,%s,%s,%s,'reconciliation',%s,'unknown_effect',
                          'reconciling',1,%s,'production recovery source',%s,3)""",
                (
                    case_id,
                    authority["workspace_id"],
                    cycle["goal_id"],
                    cycle["cycle_id"],
                    cycle["context_id"],
                    cycle["operation_id"],
                    "e" * 64,
                    authority["subject_id"],
                    case_deadline,
                ),
            )
            cycle_case = _insert_0047_recovery_work(
                connection,
                authority,
                source_discriminant="cycle_case",
                source_id=case_id,
                source_revision=case_revision,
                case_revision=case_revision,
                eligibility_revision=enrollment["eligibility_revision"],
                cycle_id=cycle["cycle_id"],
                context_id=cycle["context_id"],
                operation_id=cycle["operation_id"],
                target_alias="production-provider-1",
                remote_account_id="production-account-1",
                remote_resource_id=None,
                reason="unknown_effect",
                state="reconciling",
                deadline=case_deadline,
                request_fingerprint=request_fingerprint,
            )
            assert cycle_case["source_id"] == case_id
            assert cycle_case["source_fingerprint"] != "0" * 64
            assert cycle_case["snapshot"] is None
            assert (cycle_case["reason"], cycle_case["state"]) == connection.execute(
                "SELECT failure_class,state FROM v4_recovery_cases WHERE id=%s",
                (case_id,),
            ).fetchone()
            mismatched_case_payload = Jsonb({"case_id": str(uuid4())})
            assert _postgres_statement_rejected(
                connection,
                "cycle case_id.*source|case_id.*work source",
                lambda: connection.execute(
                    """INSERT INTO v4_production_recovery_events(
                              event_id,work_id,event_key,event_kind,occurred_at,
                              actor_id,policy_decision_id,payload,payload_sha256)
                       VALUES(%s,%s,%s,'held_conflict',clock_timestamp(),%s,%s,%s,
                              encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                    (
                        uuid4(),
                        cycle_case["work_id"],
                        "work:" + str(cycle_case["work_id"]) + ":wrong-case",
                        authority["subject_id"],
                        uuid4(),
                        mismatched_case_payload,
                        mismatched_case_payload,
                    ),
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "exact current source",
                lambda: _insert_0047_recovery_work(
                    connection,
                    authority,
                    source_discriminant="cycle_case",
                    source_id=case_id,
                    source_revision=case_revision,
                    case_revision=case_revision,
                    eligibility_revision=enrollment["eligibility_revision"],
                    cycle_id=cycle["cycle_id"],
                    context_id=cycle["context_id"],
                    operation_id=cycle["operation_id"],
                    target_alias="production-provider-1",
                    remote_account_id="production-account-1",
                    remote_resource_id=None,
                    reason="manual_review",
                    state="reconciling",
                    deadline=case_deadline,
                    request_fingerprint=request_fingerprint,
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "remote resource identity must match persisted production receipt evidence",
                lambda: _insert_0047_recovery_work(
                    connection,
                    authority,
                    source_discriminant="cycle_case",
                    source_id=case_id,
                    source_revision=case_revision,
                    case_revision=case_revision,
                    eligibility_revision=enrollment["eligibility_revision"],
                    cycle_id=cycle["cycle_id"],
                    context_id=cycle["context_id"],
                    operation_id=cycle["operation_id"],
                    target_alias="production-provider-1",
                    remote_account_id="production-account-1",
                    remote_resource_id="not-in-persisted-receipts",
                    reason="unknown_effect",
                    state="reconciling",
                    deadline=case_deadline,
                    request_fingerprint=request_fingerprint,
                ),
            )

            preadmission_intent_id = uuid4()
            due_at, expires_at = connection.execute(
                "SELECT clock_timestamp(),clock_timestamp()+interval '1 day'"
            ).fetchone()
            connection.execute(
                """INSERT INTO v4_cycle_intents(
                          id,goal_id,goal_revision,slot,fingerprint,due_at,expires_at)
                   VALUES(%s,%s,1,%s,%s,%s,%s)""",
                (
                    preadmission_intent_id,
                    cycle["goal_id"],
                    "pre-admission-" + str(preadmission_intent_id),
                    "1" * 64,
                    due_at,
                    expires_at,
                ),
            )
            preadmission_enrollment = _insert_0047_enrollment(
                connection, authority, intent_id=preadmission_intent_id
            )
            connection.execute(
                """INSERT INTO v4_admissions(
                          id,intent_id,eligibility_revision,disposition,reason,cycle_id)
                   VALUES(%s,%s,%s,'deferred','wait for due time',NULL)""",
                (
                    uuid4(),
                    preadmission_intent_id,
                    preadmission_enrollment["eligibility_revision"],
                ),
            )
            preadmission = _insert_0047_recovery_work(
                connection,
                authority,
                source_discriminant="pre_admission_intent_case",
                source_id=preadmission_intent_id,
                source_revision=preadmission_enrollment["eligibility_revision"],
                intent_id=preadmission_intent_id,
                eligibility_revision=preadmission_enrollment["eligibility_revision"],
                target_alias=None,
                remote_account_id=None,
                remote_resource_id=None,
                reason="admission_deferred",
                state="unadmitted",
                deadline=expires_at,
                due_at=due_at,
                expires_at=expires_at,
                request_fingerprint="1" * 64,
            )
            assert preadmission["source_id"] == preadmission_intent_id
            assert preadmission["cycle_id"] is None
            assert preadmission["context_id"] is None
            assert preadmission["operation_id"] is None
            assert preadmission["target_alias"] is None
            assert preadmission["snapshot"] is None
            assert (preadmission["reason"], preadmission["state"]) == (
                "admission_deferred",
                "unadmitted",
            )
            assert _postgres_statement_rejected(
                connection,
                "canonical admission disposition",
                lambda: _insert_0047_recovery_work(
                    connection,
                    authority,
                    source_discriminant="pre_admission_intent_case",
                    source_id=preadmission_intent_id,
                    source_revision=preadmission_enrollment["eligibility_revision"],
                    intent_id=preadmission_intent_id,
                    eligibility_revision=preadmission_enrollment["eligibility_revision"],
                    reason="admission_review_required",
                    state="unadmitted",
                    deadline=expires_at,
                    due_at=due_at,
                    expires_at=expires_at,
                    request_fingerprint="1" * 64,
                ),
            )

            hold_snapshot, hold_hash = _production_snapshot_fixture(
                connection, authority, enrollment
            )
            hold = _insert_0047_recovery_work(
                connection,
                authority,
                source_discriminant="production_publication_hold",
                source_id=cycle["operation_id"],
                source_revision=1,
                eligibility_revision=enrollment["eligibility_revision"],
                cycle_id=cycle["cycle_id"],
                context_id=cycle["context_id"],
                operation_id=cycle["operation_id"],
                target_alias="production-provider-1",
                remote_account_id="production-account-1",
                remote_resource_id=None,
                reason="expired_permit_receipt",
                state="held",
                snapshot=hold_snapshot,
                snapshot_version="salience.p1-production-publication-liability.v1",
                snapshot_sha256=hold_hash,
                request_fingerprint=request_fingerprint,
            )
            assert hold["work_id"] != cycle["operation_id"]
            assert hold["source_id"] == hold["operation_id"]
            assert set(hold["snapshot"]) == set(_RECOVERY_V1_KEYS)
            assert (hold["reason"], hold["state"]) == (
                "expired_permit_receipt",
                "held",
            )
            assert _postgres_statement_rejected(
                connection,
                "reason/state pair does not match cycle evidence",
                lambda: _insert_0047_recovery_work(
                    connection,
                    authority,
                    source_discriminant="production_publication_hold",
                    source_id=cycle["operation_id"],
                    source_revision=1,
                    eligibility_revision=enrollment["eligibility_revision"],
                    cycle_id=cycle["cycle_id"],
                    context_id=cycle["context_id"],
                    operation_id=cycle["operation_id"],
                    target_alias="production-provider-1",
                    remote_account_id="production-account-1",
                    remote_resource_id=None,
                    reason="published_during_cancel",
                    state="held",
                    snapshot=hold_snapshot,
                    snapshot_version="salience.p1-production-publication-liability.v1",
                    snapshot_sha256=hold_hash,
                    request_fingerprint=request_fingerprint,
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "fixture target identities cannot be adopted",
                lambda: _insert_0047_recovery_work(
                    connection,
                    authority,
                    source_discriminant="cycle_case",
                    source_id=case_id,
                    source_revision=case_revision,
                    case_revision=case_revision,
                    eligibility_revision=enrollment["eligibility_revision"],
                    cycle_id=cycle["cycle_id"],
                    context_id=cycle["context_id"],
                    operation_id=cycle["operation_id"],
                    target_alias="fixture-provider",
                    remote_account_id="production-account-1",
                    remote_resource_id=None,
                    reason="unknown_effect",
                    state="reconciling",
                    deadline=case_deadline,
                    request_fingerprint=request_fingerprint,
                ),
            )

            assert _postgres_statement_rejected(
                connection,
                "exact production intent enrollment|required production enrollment",
                lambda: _insert_0047_recovery_work(
                    connection,
                    authority,
                    source_discriminant="cycle_case",
                    source_id=fixture["case_id"],
                    source_revision=2,
                    case_revision=2,
                    intent_id=fixture["cycle"]["intent_id"],
                    eligibility_revision=1,
                    cycle_id=fixture["cycle"]["cycle_id"],
                    context_id=fixture["cycle"]["context_id"],
                    operation_id=fixture["cycle"]["operation_id"],
                    target_alias="production-provider-1",
                    remote_account_id="production-account-1",
                    remote_resource_id="unmarked-source-resource",
                    reason="manual_review",
                    state="awaiting_review",
                    request_fingerprint="a" * 64,
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "source_id.*operation_id|production publication hold source identity",
                lambda: _insert_0047_recovery_work(
                    connection,
                    authority,
                    source_discriminant="production_publication_hold",
                    source_id=uuid4(),
                    source_revision=1,
                    eligibility_revision=enrollment["eligibility_revision"],
                    cycle_id=cycle["cycle_id"],
                    context_id=cycle["context_id"],
                    operation_id=cycle["operation_id"],
                    target_alias="production-provider-1",
                    remote_account_id="production-account-1",
                    remote_resource_id=None,
                    reason="expired_permit_receipt",
                    state="held",
                    snapshot=hold_snapshot,
                    snapshot_version="salience.p1-production-publication-liability.v1",
                    snapshot_sha256=hold_hash,
                    request_fingerprint=request_fingerprint,
                ),
            )
            incomplete_snapshot = dict(hold_snapshot)
            incomplete_snapshot.pop("audit_events")
            assert _postgres_statement_rejected(
                connection,
                "exact 22 production liability snapshot keys|required 22-key",
                lambda: _insert_0047_recovery_work(
                    connection,
                    authority,
                    source_discriminant="production_publication_hold",
                    source_id=cycle["operation_id"],
                    source_revision=1,
                    eligibility_revision=enrollment["eligibility_revision"],
                    cycle_id=cycle["cycle_id"],
                    context_id=cycle["context_id"],
                    operation_id=cycle["operation_id"],
                    target_alias="production-provider-1",
                    remote_account_id="production-account-1",
                    remote_resource_id=None,
                    reason="expired_permit_receipt",
                    state="held",
                    snapshot=incomplete_snapshot,
                    snapshot_version="salience.p1-production-publication-liability.v1",
                    snapshot_sha256=hold_hash,
                    request_fingerprint=request_fingerprint,
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0047_recovery_events_are_append_only_and_scoped():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0047_0046_database(
        "p1_0047_recovery_events_"
    )
    try:
        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            enrollment = _insert_0047_enrollment(connection, authority)
            request_fingerprint = _production_request_fingerprint(
                connection, authority
            )
            work = _insert_0047_recovery_work(
                connection,
                authority,
                source_discriminant="production_publication_hold",
                source_id=authority["operation_id"],
                source_revision=1,
                eligibility_revision=enrollment["eligibility_revision"],
                cycle_id=authority["cycle"]["cycle_id"],
                context_id=authority["cycle"]["context_id"],
                operation_id=authority["operation_id"],
                target_alias="production-provider-1",
                remote_account_id="production-account-1",
                remote_resource_id=None,
                reason="expired_permit_receipt",
                state="held",
                snapshot=_production_snapshot_fixture(connection, authority, enrollment)[0],
                snapshot_version="salience.p1-production-publication-liability.v1",
                snapshot_sha256=_production_snapshot_fixture(
                    connection, authority, enrollment
                )[1],
                request_fingerprint=request_fingerprint,
            )
            payload = Jsonb(
                {
                    "schema_version": "salience.p1-production-recovery-event.v1",
                    "reason_code": "expired_permit_receipt",
                    "evidence_sha256": "a" * 64,
                    "operation_id": str(authority["operation_id"]),
                }
            )
            event_id = uuid4()
            event_key = "work:" + str(work["work_id"]) + ":initial-alert"
            connection.execute(
                """INSERT INTO v4_production_recovery_events(
                          event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                          route_id,slot_id,fencing_epoch,policy_decision_id,payload,
                          payload_sha256)
                   VALUES(%s,%s,%s,'alert_intent',clock_timestamp(),%s,%s,%s,7,%s,%s,
                          encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                (
                    event_id,
                    work["work_id"],
                    event_key,
                    authority["subject_id"],
                    enrollment["route_id"],
                    enrollment["slot_id"],
                    uuid4(),
                    payload,
                    payload,
                ),
            )
            assert connection.execute(
                "SELECT event_kind FROM v4_production_recovery_events WHERE event_id=%s",
                (event_id,),
            ).fetchone()[0] == "alert_intent"

            mismatched_scope_payload = Jsonb(
                {"operation_id": str(uuid4())}
            )
            assert _postgres_statement_rejected(
                connection,
                "payload identity or route does not match",
                lambda: connection.execute(
                    """INSERT INTO v4_production_recovery_events(
                              event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                              policy_decision_id,payload,payload_sha256)
                       VALUES(%s,%s,%s,'held_conflict',clock_timestamp(),%s,%s,%s,
                              encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                    (
                        uuid4(),
                        work["work_id"],
                        event_key + ":wrong-operation",
                        authority["subject_id"],
                        uuid4(),
                        mismatched_scope_payload,
                        mismatched_scope_payload,
                    ),
                ),
            )

            assert _postgres_statement_rejected(
                connection,
                "production recovery events are append-only",
                lambda: connection.execute(
                    "UPDATE v4_production_recovery_events SET event_kind="
                    "'same_operation_no_effect_reissue' WHERE event_id=%s",
                    (event_id,),
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "production recovery events are append-only",
                lambda: connection.execute(
                    "DELETE FROM v4_production_recovery_events WHERE event_id=%s",
                    (event_id,),
                ),
            )
            duplicate_key = _postgres_statement_rejected(
                connection,
                "unique|duplicate key",
                lambda: connection.execute(
                    """INSERT INTO v4_production_recovery_events(
                              event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                              policy_decision_id,payload,payload_sha256)
                       VALUES(%s,%s,%s,'same_operation_no_effect_reissue',
                              clock_timestamp(),%s,%s,%s,%s)""",
                    (
                        uuid4(),
                        work["work_id"],
                        event_key,
                        authority["subject_id"],
                        uuid4(),
                        payload,
                        connection.execute(
                            "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex')",
                            (payload,),
                        ).fetchone()[0],
                    ),
                ),
            )
            assert duplicate_key

            assert _postgres_statement_rejected(
                connection,
                "unsafe recovery event payload key|unapproved payload key",
                lambda: connection.execute(
                    """INSERT INTO v4_production_recovery_events(
                              event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                              policy_decision_id,payload,payload_sha256)
                       VALUES(%s,%s,%s,'held_conflict',clock_timestamp(),%s,%s,%s,%s)""",
                    (
                        uuid4(),
                        work["work_id"],
                        event_key + ":unsafe",
                        authority["subject_id"],
                        uuid4(),
                        Jsonb({"access_token": "never persist"}),
                        "b" * 64,
                    ),
                ),
            )

            other_workspace_id, foreign_actor_id = _seed_0044_workspace_subject(
                connection
            )
            assert other_workspace_id != authority["workspace_id"]
            assert _postgres_statement_rejected(
                connection,
                "actor.*workspace|workspace.*actor",
                lambda: connection.execute(
                    """INSERT INTO v4_production_recovery_events(
                              event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                              policy_decision_id,payload,payload_sha256)
                       VALUES(%s,%s,%s,'held_conflict',clock_timestamp(),%s,%s,%s,
                              encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                    (
                        uuid4(),
                        work["work_id"],
                        event_key + ":cross-workspace-actor",
                        foreign_actor_id,
                        uuid4(),
                        Jsonb({"reason_code": "test"}),
                        Jsonb({"reason_code": "test"}),
                    ),
                ),
            )

            nested_secret_payload = Jsonb(
                {"reason_code": {"access_token": "never persist"}}
            )
            assert _postgres_statement_rejected(
                connection,
                "nested|scalar|bounded object",
                lambda: connection.execute(
                    """INSERT INTO v4_production_recovery_events(
                              event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                              policy_decision_id,payload,payload_sha256)
                       VALUES(%s,%s,%s,'held_conflict',clock_timestamp(),%s,%s,%s,
                              encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                    (
                        uuid4(),
                        work["work_id"],
                        event_key + ":nested-secret",
                        authority["subject_id"],
                        uuid4(),
                        nested_secret_payload,
                        nested_secret_payload,
                    ),
                ),
            )

            non_case_payload = Jsonb({"case_id": str(uuid4())})
            assert _postgres_statement_rejected(
                connection,
                "cycle case_id.*work source",
                lambda: connection.execute(
                    """INSERT INTO v4_production_recovery_events(
                              event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                              policy_decision_id,payload,payload_sha256)
                       VALUES(%s,%s,%s,'held_conflict',clock_timestamp(),%s,%s,%s,
                              encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                    (
                        uuid4(),
                        work["work_id"],
                        event_key + ":non-case-case-id",
                        authority["subject_id"],
                        uuid4(),
                        non_case_payload,
                        non_case_payload,
                    ),
                ),
            )

            assert _postgres_statement_rejected(
                connection,
                "does not belong to the matching production recovery work|event work scope",
                lambda: connection.execute(
                    """INSERT INTO v4_production_recovery_events(
                              event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                              policy_decision_id,payload,payload_sha256)
                       VALUES(%s,%s,%s,'held_conflict',clock_timestamp(),%s,%s,%s,
                              encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                    (
                        uuid4(),
                        uuid4(),
                        event_key + ":cross-work",
                        authority["subject_id"],
                        uuid4(),
                        payload,
                        payload,
                    ),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0047_snapshot_lookup_rejects_unresolved_scope_and_binding_ids():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0047_0046_database(
        "p1_0047_snapshot_scope_"
    )
    try:
        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            _insert_0047_enrollment(connection, authority)
            job_id, foreign_job_id = uuid4(), uuid4()
            request_id, foreign_request_id = uuid4(), uuid4()
            plan_id, other_plan_id = uuid4(), uuid4()
            attempt_id, other_attempt_id = uuid4(), uuid4()
            external_effect_id = uuid4()
            foreign_workspace_effect_id = uuid4()
            wrong_job_effect_id = uuid4()
            unrelated_effect_id = uuid4()
            valid_budget_id, foreign_budget_id = uuid4(), uuid4()
            valid_reservation_id = uuid4()
            foreign_workspace_reservation_id = uuid4()
            wrong_job_reservation_id = uuid4()
            wrong_effect_reservation_id = uuid4()
            other_workspace_id, _ = _seed_0044_workspace_subject(connection)
            program_id = authority["cycle"]["program_id"]

            connection.execute("SET session_replication_role = replica")
            for current_job_id, workspace_id in (
                (job_id, authority["workspace_id"]),
                (foreign_job_id, other_workspace_id),
            ):
                connection.execute(
                    """INSERT INTO jobs(
                              id,workspace_id,job_type,workflow_run_id,task_queue,
                              idempotency_key,trace_id)
                       VALUES(%s,%s,'recovery_snapshot','workflow-'||%s::text,
                              'local','job-'||%s::text,'trace')""",
                    (current_job_id, workspace_id, current_job_id, current_job_id),
                )

            request_columns = """(
                id,workspace_id,content_program_id,ready_package_id,
                publisher_account_id,request_key,version,publisher_id,platform,
                destination,locale,territory,visibility,capability_profile_version,
                approval_reference,state,request_fingerprint,disclosure_projection,
                policy_references,rights_references)"""
            for current_request_id, workspace_id, request_key in (
                (request_id, authority["workspace_id"], "snapshot-valid"),
                (foreign_request_id, other_workspace_id, "snapshot-foreign"),
            ):
                connection.execute(
                    """INSERT INTO publication_requests""" + request_columns + """
                       VALUES(%s,%s,%s,%s,%s,%s,1,'p1-local','p1-local',
                              'channel','en-US','US','public',1,'test','planned',
                              %s,'{}','[]','[]')""",
                    (
                        current_request_id,
                        workspace_id,
                        program_id,
                        uuid4(),
                        uuid4(),
                        request_key,
                        "f" * 64,
                    ),
                )

            connection.execute(
                """INSERT INTO external_effects(
                          id,workspace_id,job_id,effect_type,effect_classification,
                          idempotency_key,request_fingerprint)
                   VALUES(%s,%s,%s,'publication','write','snapshot-effect',%s),
                         (%s,%s,%s,'publication','write','foreign-workspace-effect',%s),
                         (%s,%s,%s,'publication','write','wrong-job-effect',%s),
                         (%s,%s,%s,'publication','write','unrelated-effect',%s)""",
                (
                    external_effect_id, authority["workspace_id"], job_id, "c" * 64,
                    foreign_workspace_effect_id, other_workspace_id, job_id, "d" * 64,
                    wrong_job_effect_id, authority["workspace_id"], foreign_job_id, "e" * 64,
                    unrelated_effect_id, authority["workspace_id"], job_id, "f" * 64,
                ),
            )
            connection.execute(
                """INSERT INTO budgets(
                          id,workspace_id,content_program_id,name,scope,limit_amount)
                   VALUES(%s,%s,%s,'snapshot-valid-budget','program',100),
                         (%s,%s,NULL,'snapshot-foreign-budget','workspace',100)""",
                (
                    valid_budget_id,
                    authority["workspace_id"],
                    program_id,
                    foreign_budget_id,
                    other_workspace_id,
                ),
            )
            connection.execute(
                """INSERT INTO budget_reservations(
                          id,budget_id,job_id,external_effect_id,reservation_key,
                          estimated_amount,reserved_amount)
                   VALUES(%s,%s,%s,%s,'snapshot-valid-reservation',1,1),
                         (%s,%s,%s,%s,'snapshot-foreign-workspace-reservation',1,1),
                         (%s,%s,%s,%s,'snapshot-wrong-job-reservation',1,1),
                         (%s,%s,%s,%s,'snapshot-wrong-effect-reservation',1,1)""",
                (
                    valid_reservation_id, valid_budget_id, job_id, external_effect_id,
                    foreign_workspace_reservation_id, foreign_budget_id, job_id,
                    external_effect_id,
                    wrong_job_reservation_id, valid_budget_id, foreign_job_id,
                    external_effect_id,
                    wrong_effect_reservation_id, valid_budget_id, job_id,
                    unrelated_effect_id,
                ),
            )
            connection.execute(
                """INSERT INTO publication_plans(
                          id,publication_request_id,version,publisher_id,
                          publisher_version,external_effect_id,budget_reservation_id,state)
                   VALUES(%s,%s,1,'p1-local','1',%s,%s,'planned'),
                         (%s,%s,2,'p1-local','1',NULL,NULL,'planned')""",
                (
                    plan_id, request_id, external_effect_id, valid_reservation_id,
                    other_plan_id, request_id,
                ),
            )
            connection.execute(
                """INSERT INTO publication_attempts(
                          id,publication_plan_id,attempt_number,idempotency_key,
                          state,request_fingerprint)
                   VALUES(%s,%s,1,'snapshot-attempt-1','planned',%s),
                         (%s,%s,1,'snapshot-attempt-2','planned',%s)""",
                (
                    attempt_id,
                    plan_id,
                    "a" * 64,
                    other_attempt_id,
                    other_plan_id,
                    "b" * 64,
                ),
            )

            def replace_request_snapshot(snapshot):
                connection.execute(
                    "UPDATE v4_production_effect_permits SET request_snapshot=%s "
                    "WHERE permit_id=%s",
                    (Jsonb(snapshot), authority["permit_id"]),
                )

            base_snapshot = dict(
                connection.execute(
                    "SELECT request_snapshot FROM v4_production_effect_permits "
                    "WHERE permit_id=%s",
                    (authority["permit_id"],),
                ).fetchone()[0]
            )
            base_snapshot.update(
                {
                    "job_id": str(job_id),
                    "publication_request_id": str(request_id),
                    "publication_plan_id": str(plan_id),
                    "publication_attempt_id": str(attempt_id),
                }
            )
            replace_request_snapshot(base_snapshot)
            valid = connection.execute(
                "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                (authority["operation_id"],),
            ).fetchone()[0]
            assert valid["job"]["id"] == str(job_id)
            assert valid["publication_request"]["id"] == str(request_id)
            assert valid["publication_plan"]["id"] == str(plan_id)
            assert valid["publication_attempt"]["id"] == str(attempt_id)
            assert valid["external_effect"]["id"] == str(external_effect_id)
            assert valid["budget_reservation"]["id"] == str(valid_reservation_id)

            # job_id remains optional, but its omission must not bypass validation of
            # the linked effect or budget reservation.
            plan_without_job_id = dict(base_snapshot)
            del plan_without_job_id["job_id"]
            replace_request_snapshot(plan_without_job_id)
            plan_without_job_snapshot = connection.execute(
                "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                (authority["operation_id"],),
            ).fetchone()[0]
            assert plan_without_job_snapshot["job"] is None
            assert plan_without_job_snapshot["external_effect"]["id"] == str(
                external_effect_id
            )
            assert plan_without_job_snapshot["budget_reservation"]["id"] == str(
                valid_reservation_id
            )

            uppercase_identifiers = dict(
                base_snapshot,
                publication_plan_id=str(plan_id).upper(),
                publication_attempt_id=str(attempt_id).upper(),
            )
            replace_request_snapshot(uppercase_identifiers)
            uppercase_snapshot = connection.execute(
                "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                (authority["operation_id"],),
            ).fetchone()[0]
            assert uppercase_snapshot["publication_plan"]["id"] == str(plan_id)
            assert uppercase_snapshot["publication_attempt"]["id"] == str(attempt_id)

            cross_workspace_job = dict(base_snapshot, job_id=str(foreign_job_id))
            replace_request_snapshot(cross_workspace_job)
            assert _postgres_statement_rejected(
                connection,
                "job_id.*workspace|unresolved.*job_id",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            malformed_job = dict(base_snapshot, job_id="not-a-uuid")
            replace_request_snapshot(malformed_job)
            assert _postgres_statement_rejected(
                connection,
                "malformed.*job_id",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            cross_workspace_request = dict(
                base_snapshot, publication_request_id=str(foreign_request_id)
            )
            replace_request_snapshot(cross_workspace_request)
            assert _postgres_statement_rejected(
                connection,
                "publication_request_id.*workspace|unresolved.*publication_request_id",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            malformed_request = dict(
                base_snapshot, publication_request_id="not-a-uuid"
            )
            replace_request_snapshot(malformed_request)
            assert _postgres_statement_rejected(
                connection,
                "malformed.*publication_request_id",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            mismatched_plan = dict(base_snapshot, publication_plan_id=str(uuid4()))
            replace_request_snapshot(mismatched_plan)
            assert _postgres_statement_rejected(
                connection,
                "publication_plan_id.*request|unresolved.*publication_plan_id",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            mismatched_job_plan = dict(
                base_snapshot, publication_plan_id=str(other_plan_id)
            )
            del mismatched_job_plan["publication_attempt_id"]
            replace_request_snapshot(mismatched_job_plan)
            assert _postgres_statement_rejected(
                connection,
                "job_id.*external effect",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            request_selected_plan = dict(base_snapshot)
            del request_selected_plan["publication_plan_id"]
            del request_selected_plan["publication_attempt_id"]
            replace_request_snapshot(request_selected_plan)
            assert _postgres_statement_rejected(
                connection,
                "job_id.*external effect",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            # Omitting job_id must not skip effect/job/workspace validation.
            plan_without_job_id = dict(base_snapshot)
            del plan_without_job_id["job_id"]
            for bad_effect_id in (foreign_workspace_effect_id, wrong_job_effect_id):
                connection.execute(
                    "UPDATE publication_plans SET external_effect_id=%s,"
                    "budget_reservation_id=NULL WHERE id=%s",
                    (bad_effect_id, plan_id),
                )
                replace_request_snapshot(plan_without_job_id)
                assert _postgres_statement_rejected(
                    connection,
                    "external effect.*workspace|external effect.*job|snapshot.*effect",
                    lambda: connection.execute(
                        "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                        (authority["operation_id"],),
                    ).fetchone(),
                )
            connection.execute(
                "UPDATE publication_plans SET external_effect_id=%s WHERE id=%s",
                (external_effect_id, plan_id),
            )

            explicit_wrong_effect = dict(
                base_snapshot, external_effect_id=str(unrelated_effect_id)
            )
            replace_request_snapshot(explicit_wrong_effect)
            assert _postgres_statement_rejected(
                connection,
                "external_effect_id.*plan|external effect.*binding",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )
            unbound_explicit_effect = dict(
                base_snapshot,
                external_effect_id=str(external_effect_id),
            )
            del unbound_explicit_effect["publication_plan_id"]
            del unbound_explicit_effect["publication_attempt_id"]
            replace_request_snapshot(unbound_explicit_effect)
            assert _postgres_statement_rejected(
                connection,
                "external_effect_id.*plan|cannot resolve.*external effect",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            # The reservation must resolve through a same-workspace budget and
            # carry the exact plan effect and its job.
            for bad_reservation_id in (
                foreign_workspace_reservation_id,
                wrong_job_reservation_id,
                wrong_effect_reservation_id,
            ):
                connection.execute(
                    "UPDATE publication_plans SET budget_reservation_id=%s WHERE id=%s",
                    (bad_reservation_id, plan_id),
                )
                replace_request_snapshot(base_snapshot)
                assert _postgres_statement_rejected(
                    connection,
                    "budget reservation.*workspace|budget reservation.*job|budget reservation.*effect",
                    lambda: connection.execute(
                        "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                        (authority["operation_id"],),
                    ).fetchone(),
                )
            connection.execute(
                "UPDATE publication_plans SET budget_reservation_id=%s WHERE id=%s",
                (valid_reservation_id, plan_id),
            )
            explicit_wrong_reservation = dict(
                base_snapshot,
                budget_reservation_id=str(foreign_workspace_reservation_id),
            )
            replace_request_snapshot(explicit_wrong_reservation)
            assert _postgres_statement_rejected(
                connection,
                "budget_reservation_id.*plan|budget reservation.*binding",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            unbound_explicit_reservation = dict(
                base_snapshot,
                budget_reservation_id=str(valid_reservation_id),
            )
            del unbound_explicit_reservation["publication_plan_id"]
            del unbound_explicit_reservation["publication_attempt_id"]
            replace_request_snapshot(unbound_explicit_reservation)
            assert _postgres_statement_rejected(
                connection,
                "budget_reservation_id.*plan|cannot resolve.*budget reservation",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            wrong_budget_binding = dict(
                base_snapshot, budget_id=str(foreign_budget_id)
            )
            replace_request_snapshot(wrong_budget_binding)
            assert _postgres_statement_rejected(
                connection,
                "budget_id.*reservation",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            mismatched_attempt = dict(
                base_snapshot, publication_attempt_id=str(other_attempt_id)
            )
            replace_request_snapshot(mismatched_attempt)
            assert _postgres_statement_rejected(
                connection,
                "publication_attempt_id.*plan|unresolved.*publication_attempt_id",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            malformed_attempt = dict(
                base_snapshot, publication_attempt_id="not-a-uuid"
            )
            replace_request_snapshot(malformed_attempt)
            assert _postgres_statement_rejected(
                connection,
                "malformed.*publication_attempt_id",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )

            malformed_plan = dict(base_snapshot, publication_plan_id="not-a-uuid")
            replace_request_snapshot(malformed_plan)
            assert _postgres_statement_rejected(
                connection,
                "malformed.*publication_plan_id|unresolved.*publication_plan_id",
                lambda: connection.execute(
                    "SELECT v4_production_recovery_publication_snapshot_v1(%s)",
                    (authority["operation_id"],),
                ).fetchone(),
            )
            connection.execute("SET session_replication_role = origin")
    finally:
        _drop_migration_database(source, name)


def test_0047_downgrade_refuses_populated_recovery_history():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0047_0046_database(
        "p1_0047_populated_downgrade_"
    )
    try:
        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            enrollment = _insert_0047_enrollment(connection, authority)
            snapshot, snapshot_hash = _production_snapshot_fixture(
                connection, authority, enrollment
            )
            request_fingerprint = _production_request_fingerprint(
                connection, authority
            )
            work = _insert_0047_recovery_work(
                connection,
                authority,
                source_discriminant="production_publication_hold",
                source_id=authority["operation_id"],
                source_revision=1,
                eligibility_revision=enrollment["eligibility_revision"],
                cycle_id=authority["cycle"]["cycle_id"],
                context_id=authority["cycle"]["context_id"],
                operation_id=authority["operation_id"],
                target_alias="production-provider-1",
                remote_account_id="production-account-1",
                remote_resource_id=None,
                reason="expired_permit_receipt",
                state="held",
                snapshot=snapshot,
                snapshot_version="salience.p1-production-publication-liability.v1",
                snapshot_sha256=snapshot_hash,
                request_fingerprint=request_fingerprint,
            )
            payload = Jsonb({"reason_code": "expired_permit_receipt"})
            connection.execute(
                """INSERT INTO v4_production_recovery_events(
                          event_id,work_id,event_key,event_kind,occurred_at,actor_id,
                          route_id,slot_id,fencing_epoch,policy_decision_id,payload,
                          payload_sha256)
                   VALUES(%s,%s,'downgrade-proof','alert_intent',clock_timestamp(),
                          %s,%s,%s,7,%s,%s,
                          encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                (
                    uuid4(),
                    work["work_id"],
                    authority["subject_id"],
                    enrollment["route_id"],
                    enrollment["slot_id"],
                    uuid4(),
                    payload,
                    payload,
                ),
            )
            before_rows = connection.execute(
                """SELECT
                      (SELECT count(*) FROM v4_production_intent_enrollments),
                      (SELECT count(*) FROM v4_production_recovery_work),
                      (SELECT count(*) FROM v4_production_recovery_events),
                      (SELECT to_jsonb(e) FROM v4_production_intent_enrollments e
                       WHERE intent_id=%s AND eligibility_revision=%s),
                      (SELECT to_jsonb(w) FROM v4_production_recovery_work w
                       WHERE work_id=%s),
                      (SELECT version_num FROM alembic_version)""",
                (
                    enrollment["intent_id"],
                    enrollment["eligibility_revision"],
                    work["work_id"],
                ),
            ).fetchone()
            schema_before = _capture_0047_new_schema_contract(connection)
        downgrade = _migration(database, "downgrade", _REVISION_0046)
        assert downgrade.returncode != 0
        assert "preserve production recovery enrollment and work history" in (
            downgrade.stdout + downgrade.stderr
        )
        with psycopg.connect(database) as connection:
            after_rows = connection.execute(
                """SELECT
                      (SELECT count(*) FROM v4_production_intent_enrollments),
                      (SELECT count(*) FROM v4_production_recovery_work),
                      (SELECT count(*) FROM v4_production_recovery_events),
                      (SELECT to_jsonb(e) FROM v4_production_intent_enrollments e
                       WHERE intent_id=%s AND eligibility_revision=%s),
                      (SELECT to_jsonb(w) FROM v4_production_recovery_work w
                       WHERE work_id=%s),
                      (SELECT version_num FROM alembic_version)""",
                (
                    enrollment["intent_id"],
                    enrollment["eligibility_revision"],
                    work["work_id"],
                ),
            ).fetchone()
            assert after_rows == before_rows
            assert _capture_0047_new_schema_contract(connection) == schema_before
            for relation in (
                "v4_production_intent_enrollments",
                "v4_production_recovery_work",
                "v4_production_recovery_events",
            ):
                assert connection.execute(
                    "SELECT to_regclass(%s) IS NOT NULL",
                    ("public." + relation,),
                ).fetchone()[0]
    finally:
        _drop_migration_database(source, name)


def test_0047_empty_downgrade_restores_0046_head():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, before, _, _ = _prepare_0047_0046_database(
        "p1_0047_empty_downgrade_"
    )
    try:
        _upgrade_0047_or_fail(database)
        downgraded = _migration(database, "downgrade", _REVISION_0046)
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0046
            _assert_0043_fixture_contract(connection, before)
            for relation in (
                "v4_production_intent_enrollments",
                "v4_production_recovery_work",
                "v4_production_recovery_events",
            ):
                assert connection.execute(
                    "SELECT to_regclass(%s)", ("public." + relation,)
                ).fetchone()[0] is None
            for function in (
                "v4_validate_production_intent_enrollment_v1()",
                "v4_production_recovery_publication_snapshot_v1(uuid)",
                "v4_validate_production_recovery_work_v1()",
                "v4_validate_production_recovery_event_v1()",
                "v4_reject_production_recovery_history_change_v1()",
            ):
                assert connection.execute(
                    "SELECT to_regprocedure(%s)", ("public." + function,)
                ).fetchone()[0] is None
    finally:
        _drop_migration_database(source, name)


_REVISION_0048 = "0048_p1_production_schedule_bindings"
_PRODUCTION_SCHEDULE_INVENTORY_SCHEMA = "salience.p1-production-schedule-inventory.v1"
_PRODUCTION_SCHEDULE_MEMBER_SCHEMA = "salience.p1-production-schedule-inventory-member.v1"
_PRODUCTION_SCHEDULE_APPROVAL_SCHEMA = (
    "salience.p1-production-schedule-binding-approval.v1"
)
_PRODUCTION_SCHEDULE_MEMBER_KEYS = (
    "schema_version",
    "target_alias",
    "namespace",
    "remote_schedule_id",
    "classification",
    "source_kind",
    "schedule_id",
    "publication_schedule_id",
    "workspace_id",
    "goal_id",
    "goal_revision",
    "source_actor_id",
    "source_owner_subject_id",
    "schedule_operator_subject_id",
    "owner_ref",
    "exclusion_reason",
    "workflow_type",
    "action_id",
    "argument_sha256",
    "task_queue",
    "route_id",
    "route_tuple_sha256",
    "slot_id",
    "owner_id",
    "fencing_epoch",
    "route_manifest_sha256",
    "artifact_lock_sha256",
    "cadence_policy_revision",
    "interval_seconds",
    "first_v4_slot",
    "observed_last_slot",
    "remote_state",
    "next_action",
    "running_action_count",
    "history_page_count",
    "history_action_count",
    "history_page_chain_sha256",
    "history_action_set_sha256",
    "history_watermark",
    "history_watermark_sha256",
)
_PRODUCTION_SCHEDULE_APPROVAL_KEYS = (
    "schema_version",
    "target_alias",
    "namespace",
    "inventory_id",
    "inventory_sha256",
    "source_kind",
    "schedule_id",
    "publication_schedule_id",
    "workspace_id",
    "goal_id",
    "goal_revision",
    "source_actor_id",
    "source_owner_subject_id",
    "schedule_operator_subject_id",
    "remote_schedule_id",
    "task_queue",
    "workflow_type",
    "action_id",
    "argument_sha256",
    "route_id",
    "route_tuple_sha256",
    "slot_id",
    "owner_id",
    "fencing_epoch",
    "route_manifest_sha256",
    "artifact_lock_sha256",
    "cadence_policy_revision",
    "first_v4_slot",
    "history_watermark_sha256",
    "policy_version_id",
    "policy_document_hash",
    "expires_at",
)


def _prepare_0048_database(prefix):
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, fixture_state, authority = _prepare_0047_0046_database(prefix)
    try:
        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            fixture_contract = _capture_0043_fixture_contract(connection)
        return name, database, fixture_contract, fixture_state, authority
    except Exception:
        _drop_migration_database(source, name)
        raise


def _upgrade_0048_or_fail(database):
    upgraded = _migration(database, "upgrade", _REVISION_0048)
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    return upgraded


def _production_schedule_sha256_jsonb(connection, value):
    return connection.execute(
        "SELECT encode(public.digest(convert_to(%s::jsonb::text,'UTF8'),'sha256'),'hex')",
        (Jsonb(value),),
    ).fetchone()[0]


def _production_schedule_sha256_text(connection, value):
    return connection.execute(
        "SELECT encode(public.digest(convert_to(%s,'UTF8'),'sha256'),'hex')",
        (value,),
    ).fetchone()[0]


def _insert_0048_local_source(connection, authority, *, source_kind):
    workspace_id = authority["workspace_id"]
    source_actor_id = authority["subject_id"]
    program_id = authority["cycle"]["program_id"]
    goal_id = uuid4()
    schedule_id = uuid4()
    publication_schedule_id = uuid4() if source_kind == "governed_publication" else None
    source_owner_subject_id = uuid4()
    schedule_operator_subject_id = uuid4()
    for subject_id, suffix in (
        (source_owner_subject_id, "owner"),
        (schedule_operator_subject_id, "operator"),
    ):
        connection.execute(
            """INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at)
               VALUES(%s,%s,'https://production-schedule.invalid',%s,
                      clock_timestamp()+interval '1 day')""",
            (subject_id, workspace_id, suffix + ":" + str(subject_id)),
        )
    connection.execute(
        "INSERT INTO v4_goals(id,workspace_id,state) VALUES(%s,%s,'active')",
        (goal_id, workspace_id),
    )
    connection.execute(
        """INSERT INTO v4_goal_revisions(goal_id,revision,payload)
           VALUES(%s,1,%s)""",
        (
            goal_id,
            Jsonb(
                {
                    "schema_version": "GoalSpec.local.v1",
                    "content_program_id": str(program_id),
                    "cadence": {"interval_seconds": 60},
                }
            ),
        ),
    )
    job_type = (
        "intelligence_research"
        if source_kind == "intelligence_research"
        else "governed_publication"
    )
    payload = (
        {"dry_run": True, "niche": "Production schedule test"}
        if source_kind == "intelligence_research"
        else {"publication_request_id": str(uuid4())}
    )
    first_v4_slot = datetime(2099, 1, 1, tzinfo=timezone.utc)
    connection.execute(
        """INSERT INTO job_schedules(
                  id,workspace_id,content_program_id,name,schedule_expression,
                  timezone,job_type,payload,next_run_at,status)
           VALUES(%s,%s,%s,%s,'every 60s','UTC',%s,%s,%s,'active')""",
        (
            schedule_id,
            workspace_id,
            program_id,
            "p1-0048-" + uuid4().hex,
            job_type,
            Jsonb(payload),
            first_v4_slot,
        ),
    )
    original_identity = connection.execute(
        """SELECT to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at']
           FROM job_schedules schedule WHERE id=%s""",
        (schedule_id,),
    ).fetchone()[0]
    remote_schedule_id = (
        str(schedule_id)
        if publication_schedule_id is None
        else "publication:" + str(publication_schedule_id)
    )
    connection.execute("SET session_replication_role = replica")
    if publication_schedule_id is not None:
        connection.execute(
            """INSERT INTO publication_schedules(
                      id,publication_request_id,publication_plan_id,job_schedule_id,
                      version,scheduled_for,timezone,schedule_fingerprint,status)
               VALUES(%s,%s,%s,%s,1,%s,'UTC',%s,'scheduled')""",
            (
                publication_schedule_id,
                uuid4(),
                uuid4(),
                schedule_id,
                first_v4_slot,
                "a" * 64,
            ),
        )
        connection.execute(
            """INSERT INTO v4_native_publication_schedule_sources(
                      schedule_id,publication_schedule_id,workspace_id,goal_id,
                      actor_id,goal_revision,remote_id,task_queue,interval_seconds,
                      first_v4_slot,observed_last_slot,remote_snapshot,
                      original_identity,publication_identity)
               VALUES(%s,%s,%s,%s,%s,1,%s,'salience-v4-local-legacy-test',60,
                      %s,NULL,%s,%s,%s)""",
            (
                schedule_id,
                publication_schedule_id,
                workspace_id,
                goal_id,
                source_actor_id,
                remote_schedule_id,
                first_v4_slot,
                Jsonb({"action_count": 0}),
                Jsonb(original_identity),
                Jsonb(
                    {
                        "id": str(publication_schedule_id),
                        "job_schedule_id": str(schedule_id),
                        "status": "scheduled",
                    }
                ),
            ),
        )
        connection.execute(
            """INSERT INTO v4_native_publication_schedule_progress(
                      schedule_id,last_slot,resume_after,next_slot)
               VALUES(%s,NULL,NULL,%s)""",
            (schedule_id, first_v4_slot),
        )
    else:
        connection.execute(
            """INSERT INTO v4_native_schedule_sources(
                      schedule_id,workspace_id,goal_id,actor_id,goal_revision,
                      remote_id,task_queue,interval_seconds,first_v4_slot,
                      observed_last_slot,remote_snapshot,original_identity)
               VALUES(%s,%s,%s,%s,1,%s,'salience-v4-local-legacy-test',60,
                      %s,NULL,%s,%s)""",
            (
                schedule_id,
                workspace_id,
                goal_id,
                source_actor_id,
                remote_schedule_id,
                first_v4_slot,
                Jsonb({"action_count": 0}),
                Jsonb(original_identity),
            ),
        )
        connection.execute(
            """INSERT INTO v4_native_schedule_progress(
                      schedule_id,last_slot,resume_after,next_slot)
               VALUES(%s,NULL,NULL,%s)""",
            (schedule_id, first_v4_slot),
        )
    connection.execute("SET session_replication_role = origin")
    return {
        "source_kind": source_kind,
        "schedule_id": schedule_id,
        "publication_schedule_id": publication_schedule_id,
        "remote_schedule_id": remote_schedule_id,
        "workspace_id": workspace_id,
        "goal_id": goal_id,
        "goal_revision": 1,
        "source_actor_id": source_actor_id,
        "source_owner_subject_id": source_owner_subject_id,
        "schedule_operator_subject_id": schedule_operator_subject_id,
        "first_v4_slot": first_v4_slot,
        "interval_seconds": 60,
        "source_owner_ref": "owner-ref:" + str(source_owner_subject_id),
    }


def _insert_0048_schedule_approval(
    connection, inventory, facts, member_snapshot, context_overrides=None
):
    policy_id = _seed_0044_policy(
        connection,
        facts["workspace_id"],
        status="active",
        version="schedule-" + uuid4().hex[:8],
    )
    expiry = "2099-01-01T00:00:00Z"
    request_context = {
        "schema_version": _PRODUCTION_SCHEDULE_APPROVAL_SCHEMA,
        "target_alias": inventory["target_alias"],
        "namespace": inventory["namespace"],
        "inventory_id": str(inventory["inventory_id"]),
        "inventory_sha256": inventory["inventory_sha256"],
        "source_kind": facts["source_kind"],
        "schedule_id": str(facts["schedule_id"]),
        "publication_schedule_id": (
            None
            if facts["publication_schedule_id"] is None
            else str(facts["publication_schedule_id"])
        ),
        "workspace_id": str(facts["workspace_id"]),
        "goal_id": str(facts["goal_id"]),
        "goal_revision": facts["goal_revision"],
        "source_actor_id": str(facts["source_actor_id"]),
        "source_owner_subject_id": str(facts["source_owner_subject_id"]),
        "schedule_operator_subject_id": str(facts["schedule_operator_subject_id"]),
        "remote_schedule_id": inventory["remote_schedule_id"],
        "task_queue": member_snapshot["task_queue"],
        "workflow_type": member_snapshot["workflow_type"],
        "action_id": member_snapshot["action_id"],
        "argument_sha256": member_snapshot["argument_sha256"],
        "route_id": member_snapshot["route_id"],
        "route_tuple_sha256": member_snapshot["route_tuple_sha256"],
        "slot_id": member_snapshot["slot_id"],
        "owner_id": member_snapshot["owner_id"],
        "fencing_epoch": member_snapshot["fencing_epoch"],
        "route_manifest_sha256": member_snapshot["route_manifest_sha256"],
        "artifact_lock_sha256": member_snapshot["artifact_lock_sha256"],
        "cadence_policy_revision": member_snapshot["cadence_policy_revision"],
        "first_v4_slot": member_snapshot["first_v4_slot"],
        "history_watermark_sha256": member_snapshot["history_watermark_sha256"],
        "policy_version_id": str(policy_id),
        "policy_document_hash": "b" * 64,
        "expires_at": expiry,
    }
    if context_overrides:
        request_context.update(context_overrides)
    approval_id = uuid4()
    connection.execute(
        """INSERT INTO approval_requests(
                  id,workspace_id,policy_version_id,effect_type,status,
                  request_context,requested_by)
           VALUES(%s,%s,%s,'production_schedule_binding','pending',%s,
                  'schedule-operator')""",
        (approval_id, facts["workspace_id"], policy_id, Jsonb(request_context)),
    )
    connection.execute(
        """UPDATE approval_requests
           SET status='approved',decided_by='schedule-reviewer',
               decided_at=clock_timestamp()
           WHERE id=%s""",
        (approval_id,),
    )
    return approval_id, request_context


def _build_0048_inventory(
    connection,
    authority,
    *,
    source_kind="intelligence_research",
    include_binding=True,
    finalize=True,
    include_history_page=True,
    page_count=1,
    classification="bound",
    approval_context_overrides=None,
    binding_snapshot_overrides=None,
    binding_fact_overrides=None,
    bypass_binding_guard=False,
):
    facts = (
        _insert_0048_local_source(connection, authority, source_kind=source_kind)
        if classification == "bound"
        else None
    )
    inventory_id = uuid4()
    target_alias = "temporal-production-test"
    namespace = "production-test-namespace"
    remote_schedule_id = (
        facts["remote_schedule_id"] if facts is not None else "out-of-scope:" + uuid4().hex
    )
    first_v4_slot = (
        facts["first_v4_slot"].isoformat()
        if facts is not None
        else "2099-01-01T00:00:00+00:00"
    )
    history_page_items = []
    history_request = _production_schedule_sha256_text(
        connection,
        "salience.p1-production-schedule-history.start.v1:" + remote_schedule_id,
    )
    history_item_set = _production_schedule_sha256_jsonb(connection, [])
    history_response = _production_schedule_sha256_jsonb(connection, [])
    history_receipt = _production_schedule_sha256_jsonb(
        connection,
        {
            "inventory_id": str(inventory_id),
            "remote_schedule_id": remote_schedule_id,
            "page_index": 0,
            "request_cursor_sha256": history_request,
            "response_sha256": history_response,
            "item_count": 0,
            "next_cursor_sha256": None,
            "is_terminal": True,
            "item_set_sha256": history_item_set,
            "last_item_sha256": None,
        },
    )
    history_page_chain = _production_schedule_sha256_jsonb(
        connection, [history_receipt] if include_history_page else []
    )
    history_action_set = _production_schedule_sha256_jsonb(connection, [])
    history_page_count = 1 if include_history_page or classification == "bound" else 0
    member_snapshot = {
        "schema_version": _PRODUCTION_SCHEDULE_MEMBER_SCHEMA,
        "target_alias": target_alias,
        "namespace": namespace,
        "remote_schedule_id": remote_schedule_id,
        "classification": classification,
        "source_kind": None if facts is None else facts["source_kind"],
        "schedule_id": None if facts is None else str(facts["schedule_id"]),
        "publication_schedule_id": (
            None
            if facts is None or facts["publication_schedule_id"] is None
            else str(facts["publication_schedule_id"])
        ),
        "workspace_id": None if facts is None else str(facts["workspace_id"]),
        "goal_id": None if facts is None else str(facts["goal_id"]),
        "goal_revision": None if facts is None else facts["goal_revision"],
        "source_actor_id": None if facts is None else str(facts["source_actor_id"]),
        "source_owner_subject_id": (
            None if facts is None else str(facts["source_owner_subject_id"])
        ),
        "schedule_operator_subject_id": (
            None if facts is None else str(facts["schedule_operator_subject_id"])
        ),
        "owner_ref": "external-owner-ref-" + remote_schedule_id,
        "exclusion_reason": "out of production schedule scope" if facts is None else None,
        "workflow_type": (
            "IntelligenceLoopWorkflow"
            if source_kind == "intelligence_research"
            else "GovernedPublicationWorkflow"
        ),
        "action_id": remote_schedule_id + ":execution",
        "argument_sha256": "c" * 64,
        "task_queue": "production-v4-schedule-worker",
        "route_id": str(uuid4()),
        "route_tuple_sha256": "d" * 64,
        "slot_id": str(uuid4()),
        "owner_id": (
            str(facts["source_owner_subject_id"])
            if facts is not None
            else str(uuid4())
        ),
        "fencing_epoch": 7,
        "route_manifest_sha256": "e" * 64,
        "artifact_lock_sha256": "f" * 64,
        "cadence_policy_revision": "cadence-policy-v1",
        "interval_seconds": 60,
        "first_v4_slot": first_v4_slot,
        "observed_last_slot": None,
        "remote_state": "paused",
        "next_action": "2099-01-01T00:01:00+00:00",
        "running_action_count": 0,
        "history_page_count": history_page_count,
        "history_action_count": 0,
        "history_page_chain_sha256": history_page_chain,
        "history_action_set_sha256": history_action_set,
        "history_watermark": None,
        "history_watermark_sha256": None,
    }
    member_sha256 = _production_schedule_sha256_jsonb(connection, member_snapshot)
    listing_page_items = (
        [{"remote_schedule_id": remote_schedule_id, "member_sha256": member_sha256}]
        if classification != ""
        else []
    )
    listing_request = _production_schedule_sha256_text(
        connection, "salience.p1-production-schedule-inventory.start.v1"
    )
    listing_item_set = _production_schedule_sha256_jsonb(
        connection,
        [member_sha256] if listing_page_items else [],
    )
    listing_response = _production_schedule_sha256_jsonb(
        connection, listing_page_items
    )
    listing_receipt = _production_schedule_sha256_jsonb(
        connection,
        {
            "inventory_id": str(inventory_id),
            "remote_schedule_id": None,
            "page_index": 0,
            "request_cursor_sha256": listing_request,
            "response_sha256": listing_response,
            "item_count": len(listing_page_items),
            "next_cursor_sha256": None,
            "is_terminal": True,
            "item_set_sha256": listing_item_set,
            "last_item_sha256": None,
        },
    )
    member_set_sha256 = _production_schedule_sha256_jsonb(
        connection,
        [
            {"remote_schedule_id": remote_schedule_id, "member_sha256": member_sha256}
        ]
        if listing_page_items
        else [],
    )
    listing_chain_sha256 = _production_schedule_sha256_jsonb(
        connection, [listing_receipt]
    )
    captured_at = datetime(2026, 10, 28, tzinfo=timezone.utc)
    connection.execute(
        """INSERT INTO v4_production_schedule_inventories(
                  inventory_id,target_alias,namespace,captured_at,source_tool,
                  source_tool_version,candidate_sha256,configuration_sha256,
                  page_count,remote_member_count,bound_member_count,
                  out_of_scope_member_count,inventory_page_chain_sha256,
                  member_set_sha256,inventory_sha256,finalized_at)
           VALUES(%s,%s,%s,%s,'test-inventory','1',%s,%s,%s,%s,%s,%s,
                  %s,%s,%s,NULL)""",
        (
            inventory_id,
            target_alias,
            namespace,
            captured_at,
            "1" * 64,
            "2" * 64,
            page_count,
            len(listing_page_items),
            int(classification == "bound"),
            int(classification == "out_of_scope"),
            listing_chain_sha256,
            member_set_sha256,
            "0" * 64,
        ),
    )
    if listing_page_items:
        connection.execute(
            """INSERT INTO v4_production_schedule_inventory_members(
                      inventory_id,remote_schedule_id,page_index,classification,
                      member_snapshot,member_sha256,history_page_count,
                      history_action_count,history_page_chain_sha256,
                      history_action_set_sha256,history_watermark,
                      history_watermark_sha256)
               VALUES(%s,%s,0,%s,%s,%s,%s,0,%s,%s,NULL,NULL)""",
            (
                inventory_id,
                remote_schedule_id,
                classification,
                Jsonb(member_snapshot),
                member_sha256,
                history_page_count,
                history_page_chain,
                history_action_set,
            ),
        )
    connection.execute(
        """INSERT INTO v4_production_schedule_inventory_pages(
                  inventory_id,remote_schedule_id,page_index,request_cursor_sha256,
                  page_items,response_sha256,item_count,next_cursor_sha256,
                  is_terminal,item_set_sha256,last_item_sha256,page_receipt_sha256)
           VALUES(%s,NULL,0,%s,%s,%s,%s,NULL,true,%s,NULL,%s)""",
        (
            inventory_id,
            listing_request,
            Jsonb(listing_page_items),
            listing_response,
            len(listing_page_items),
            listing_item_set,
            listing_receipt,
        ),
    )
    if include_history_page and listing_page_items:
        connection.execute(
            """INSERT INTO v4_production_schedule_inventory_pages(
                      inventory_id,remote_schedule_id,page_index,request_cursor_sha256,
                      page_items,response_sha256,item_count,next_cursor_sha256,
                      is_terminal,item_set_sha256,last_item_sha256,page_receipt_sha256)
               VALUES(%s,%s,0,%s,%s,%s,0,NULL,true,%s,NULL,%s)""",
            (
                inventory_id,
                remote_schedule_id,
                history_request,
                Jsonb(history_page_items),
                history_response,
                history_item_set,
                history_receipt,
            ),
        )
    connection.execute(
        """UPDATE v4_production_schedule_inventories
           SET inventory_page_chain_sha256=%s,member_set_sha256=%s
           WHERE inventory_id=%s""",
        (listing_chain_sha256, member_set_sha256, inventory_id),
    )
    inventory_sha256 = connection.execute(
        """SELECT encode(public.digest(convert_to(jsonb_build_object(
             'schema_version',%s::text,'inventory_id',inventory_id,
             'target_alias',target_alias,'namespace',namespace,
             'captured_at',captured_at,'source_tool',source_tool,
             'source_tool_version',source_tool_version,
             'candidate_sha256',candidate_sha256,
             'configuration_sha256',configuration_sha256,
             'page_count',page_count,'remote_member_count',remote_member_count,
             'bound_member_count',bound_member_count,
             'out_of_scope_member_count',out_of_scope_member_count,
             'inventory_page_chain_sha256',inventory_page_chain_sha256,
             'member_set_sha256',member_set_sha256)::text,'UTF8'),'sha256'),'hex')
           FROM v4_production_schedule_inventories WHERE inventory_id=%s""",
        (_PRODUCTION_SCHEDULE_INVENTORY_SCHEMA, inventory_id),
    ).fetchone()[0]
    connection.execute(
        """UPDATE v4_production_schedule_inventories
           SET inventory_sha256=%s WHERE inventory_id=%s""",
        (inventory_sha256, inventory_id),
    )
    inventory = {
        "inventory_id": inventory_id,
        "target_alias": target_alias,
        "namespace": namespace,
        "remote_schedule_id": remote_schedule_id,
        "inventory_sha256": inventory_sha256,
        "member_sha256": member_sha256,
        "page_receipt_sha256": listing_receipt,
    }
    approval_id = None
    if facts is not None:
        approval_id, approval_context = _insert_0048_schedule_approval(
            connection,
            inventory,
            facts,
            member_snapshot,
            context_overrides=approval_context_overrides,
        )
        if include_binding:
            binding_snapshot = dict(member_snapshot)
            if binding_snapshot_overrides:
                binding_snapshot.update(binding_snapshot_overrides)
            binding_facts = {
                "remote_state": member_snapshot["remote_state"],
                "next_action": member_snapshot["next_action"],
                "running_action_count": member_snapshot["running_action_count"],
            }
            if binding_fact_overrides:
                binding_facts.update(binding_fact_overrides)
            if bypass_binding_guard:
                connection.execute("SET session_replication_role = replica")
            connection.execute(
                """INSERT INTO v4_production_schedule_source_bindings(
                          inventory_id,inventory_sha256,target_alias,namespace,
                          remote_schedule_id,member_sha256,source_kind,schedule_id,
                          publication_schedule_id,source_approval_id,source_actor_id,
                          source_owner_subject_id,schedule_operator_subject_id,
                          workspace_id,goal_id,goal_revision,owner_ref,task_queue,
                          workflow_type,action_id,argument_sha256,route_id,
                          route_tuple_sha256,slot_id,owner_id,fencing_epoch,
                          route_manifest_sha256,artifact_lock_sha256,
                          cadence_policy_revision,interval_seconds,first_v4_slot,
                          observed_last_slot,remote_state,next_action,
                          running_action_count,history_page_count,history_action_count,
                          history_page_chain_sha256,history_action_set_sha256,
                          history_watermark,history_watermark_sha256,member_snapshot,
                          binding_fingerprint)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    inventory_id,
                    inventory_sha256,
                    target_alias,
                    namespace,
                    remote_schedule_id,
                    member_sha256,
                    facts["source_kind"],
                    facts["schedule_id"],
                    facts["publication_schedule_id"],
                    approval_id,
                    facts["source_actor_id"],
                    facts["source_owner_subject_id"],
                    facts["schedule_operator_subject_id"],
                    facts["workspace_id"],
                    facts["goal_id"],
                    facts["goal_revision"],
                    member_snapshot["owner_ref"],
                    member_snapshot["task_queue"],
                    member_snapshot["workflow_type"],
                    member_snapshot["action_id"],
                    member_snapshot["argument_sha256"],
                    member_snapshot["route_id"],
                    member_snapshot["route_tuple_sha256"],
                    member_snapshot["slot_id"],
                    member_snapshot["owner_id"],
                    member_snapshot["fencing_epoch"],
                    member_snapshot["route_manifest_sha256"],
                    member_snapshot["artifact_lock_sha256"],
                    member_snapshot["cadence_policy_revision"],
                    member_snapshot["interval_seconds"],
                    first_v4_slot,
                    None,
                    binding_facts["remote_state"],
                    binding_facts["next_action"],
                    binding_facts["running_action_count"],
                    member_snapshot["history_page_count"],
                    member_snapshot["history_action_count"],
                    member_snapshot["history_page_chain_sha256"],
                    member_snapshot["history_action_set_sha256"],
                    None,
                    None,
                    Jsonb(binding_snapshot),
                    "0" * 64,
                ),
            )
            if bypass_binding_guard:
                connection.execute(
                    """UPDATE v4_production_schedule_source_bindings binding
                       SET binding_fingerprint=
                           public.v4_production_schedule_jsonb_sha256_v1(
                               to_jsonb(binding)-'binding_fingerprint')
                       WHERE inventory_id=%s AND remote_schedule_id=%s""",
                    (inventory_id, remote_schedule_id),
                )
                connection.execute("SET session_replication_role = origin")
    if finalize:
        connection.execute(
            "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
            (inventory_id,),
        )
    return {
        **inventory,
        "facts": facts,
        "member_snapshot": member_snapshot,
        "approval_id": approval_id,
        "approval_context": None if facts is None else approval_context,
        "history_page_receipt_sha256": history_receipt,
    }


def test_0048_upgrade_preserves_fixture_schedule_contracts():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, fixture_contract, _, _ = _prepare_0048_database(
        "p1_0048_fixture_contract_"
    )
    try:
        upgraded = _upgrade_0048_or_fail(database)
        assert upgraded.returncode == 0
        with psycopg.connect(database) as connection:
            _assert_0043_fixture_contract(connection, fixture_contract)
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0048
    finally:
        _drop_migration_database(source, name)


def test_0048_production_schedule_binding_uniqueness_and_immutability():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_binding_unique_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            adopted = _build_0048_inventory(connection, authority)
            indexes = connection.execute(
                """SELECT indexdef FROM pg_indexes
                   WHERE schemaname='public'
                     AND tablename='v4_production_schedule_source_bindings'
                   ORDER BY indexname"""
            ).fetchall()
            definitions = "\n".join(row[0] for row in indexes)
            assert any("USING btree (goal_id)" in row[0] for row in indexes)
            assert "target_alias, namespace, remote_schedule_id" in definitions
            assert connection.execute(
                """SELECT count(*) FROM v4_production_schedule_source_bindings
                   WHERE inventory_id=%s AND remote_schedule_id=%s""",
                (adopted["inventory_id"], adopted["remote_schedule_id"]),
            ).fetchone()[0] == 1
            assert _postgres_statement_rejected(
                connection,
                "immutable|finalized",
                lambda: connection.execute(
                    """UPDATE v4_production_schedule_source_bindings
                       SET task_queue='changed' WHERE inventory_id=%s""",
                    (adopted["inventory_id"],),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_finalized_inventory_page_member_binding_immutability():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_finalized_immutable_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            adopted = _build_0048_inventory(connection, authority)
            for statement, parameters in (
                (
                    "DELETE FROM v4_production_schedule_inventories WHERE inventory_id=%s",
                    (adopted["inventory_id"],),
                ),
                (
                    "UPDATE v4_production_schedule_inventory_pages SET item_count=2 WHERE inventory_id=%s",
                    (adopted["inventory_id"],),
                ),
                (
                    "UPDATE v4_production_schedule_inventory_members SET classification='out_of_scope' WHERE inventory_id=%s",
                    (adopted["inventory_id"],),
                ),
                (
                    "DELETE FROM v4_production_schedule_source_bindings WHERE inventory_id=%s",
                    (adopted["inventory_id"],),
                ),
            ):
                assert _postgres_statement_rejected(
                    connection,
                    "immutable|finalized",
                    lambda statement=statement, parameters=parameters: connection.execute(
                        statement, parameters
                    ),
                )
    finally:
        _drop_migration_database(source, name)


def test_0048_incomplete_or_fixture_derived_binding_is_rejected():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_incomplete_fixture_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            incomplete = _build_0048_inventory(
                connection, authority, include_binding=False, finalize=False
            )
            assert _postgres_statement_rejected(
                connection,
                "binding|complete|inventory",
                lambda: connection.execute(
                    "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
                    (incomplete["inventory_id"],),
                ),
            )
            member = incomplete["member_snapshot"]
            member["task_queue"] = "salience-v4-local-legacy-fixture"
            member_sha = _production_schedule_sha256_jsonb(connection, member)
            assert _postgres_statement_rejected(
                connection,
                "fixture|source|immutable|member",
                lambda: connection.execute(
                    """UPDATE v4_production_schedule_inventory_members
                       SET member_snapshot=%s,member_sha256=%s
                       WHERE inventory_id=%s AND remote_schedule_id=%s""",
                    (
                        Jsonb(member),
                        member_sha,
                        incomplete["inventory_id"],
                        incomplete["remote_schedule_id"],
                    ),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_upgrade_does_not_backfill_from_fixture_sources():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, fixture_state, authority = _prepare_0047_0046_database(
        "p1_0048_no_backfill_"
    )
    try:
        _upgrade_0047_or_fail(database)
        with psycopg.connect(database) as connection:
            local = _insert_0048_local_source(
                connection, authority, source_kind="intelligence_research"
            )
            assert connection.execute(
                "SELECT count(*) FROM v4_native_schedule_sources WHERE schedule_id=%s",
                (local["schedule_id"],),
            ).fetchone()[0] == 1
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_schedule_inventories"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_schedule_inventory_pages"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_schedule_inventory_members"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_schedule_source_bindings"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_native_schedule_sources WHERE schedule_id=%s",
                (local["schedule_id"],),
            ).fetchone()[0] == 1
    finally:
        _drop_migration_database(source, name)


def test_0048_inventory_finalize_rejects_partial_or_truncated_pages():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_partial_pages_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            partial = _build_0048_inventory(
                connection, authority, finalize=False, page_count=2
            )
            assert _postgres_statement_rejected(
                connection,
                "page|contiguous|complete|inventory",
                lambda: connection.execute(
                    "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
                    (partial["inventory_id"],),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_inventory_finalize_rejects_cursor_chain_or_terminal_mismatch():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_cursor_chain_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            broken = _build_0048_inventory(connection, authority, finalize=False)
            page = connection.execute(
                """SELECT remote_schedule_id,request_cursor_sha256,
                          response_sha256,item_count,next_cursor_sha256,is_terminal,
                          item_set_sha256,last_item_sha256
                   FROM v4_production_schedule_inventory_pages
                   WHERE inventory_id=%s AND remote_schedule_id IS NOT NULL""",
                (broken["inventory_id"],),
            ).fetchone()
            altered_request = "a" * 64
            receipt = _production_schedule_sha256_jsonb(
                connection,
                {
                    "inventory_id": str(broken["inventory_id"]),
                    "remote_schedule_id": page[0],
                    "page_index": 0,
                    "request_cursor_sha256": altered_request,
                    "response_sha256": page[2],
                    "item_count": page[3],
                    "next_cursor_sha256": page[4],
                    "is_terminal": page[5],
                    "item_set_sha256": page[6],
                    "last_item_sha256": page[7],
                },
            )
            connection.execute(
                """UPDATE v4_production_schedule_inventory_pages
                   SET request_cursor_sha256=%s,page_receipt_sha256=%s
                   WHERE inventory_id=%s AND remote_schedule_id IS NOT NULL""",
                (altered_request, receipt, broken["inventory_id"]),
            )
            assert _postgres_statement_rejected(
                connection,
                "cursor|sentinel|chain|history|complete",
                lambda: connection.execute(
                    "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
                    (broken["inventory_id"],),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_inventory_finalize_rejects_count_or_sorted_root_mismatch():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_count_root_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            broken = _build_0048_inventory(connection, authority, finalize=False)
            connection.execute(
                """UPDATE v4_production_schedule_inventories
                   SET remote_member_count=2,bound_member_count=2,
                       inventory_sha256=encode(public.digest(convert_to(
                           jsonb_build_object(
                               'schema_version','salience.p1-production-schedule-inventory.v1',
                               'inventory_id',inventory_id,
                               'target_alias',target_alias,'namespace',namespace,
                               'captured_at',captured_at,'source_tool',source_tool,
                               'source_tool_version',source_tool_version,
                               'candidate_sha256',candidate_sha256,
                               'configuration_sha256',configuration_sha256,
                               'page_count',page_count,'remote_member_count',2,
                               'bound_member_count',2,
                               'out_of_scope_member_count',out_of_scope_member_count,
                               'inventory_page_chain_sha256',inventory_page_chain_sha256,
                               'member_set_sha256',member_set_sha256)::text,'UTF8'),
                           'sha256'),'hex')
                   WHERE inventory_id=%s""",
                (broken["inventory_id"],),
            )
            assert _postgres_statement_rejected(
                connection,
                "count|member|root|digest|inventory",
                lambda: connection.execute(
                    "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
                    (broken["inventory_id"],),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_inventory_rejects_duplicate_or_unknown_members():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_duplicate_unknown_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            inventory = _build_0048_inventory(
                connection, authority, include_binding=False, finalize=False
            )
            assert _postgres_statement_rejected(
                connection,
                "duplicate|unique|member|classification",
                lambda: connection.execute(
                    """UPDATE v4_production_schedule_inventory_members
                       SET classification='unknown'
                       WHERE inventory_id=%s AND remote_schedule_id=%s""",
                    (inventory["inventory_id"], inventory["remote_schedule_id"]),
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "duplicate|unique",
                lambda: connection.execute(
                    """INSERT INTO v4_production_schedule_inventory_members(
                              inventory_id,remote_schedule_id,page_index,classification,
                              member_snapshot,member_sha256,history_page_count,
                              history_action_count,history_page_chain_sha256,
                              history_action_set_sha256)
                       SELECT inventory_id,remote_schedule_id,page_index,classification,
                              member_snapshot,member_sha256,history_page_count,
                              history_action_count,history_page_chain_sha256,
                              history_action_set_sha256
                       FROM v4_production_schedule_inventory_members
                       WHERE inventory_id=%s""",
                    (inventory["inventory_id"],),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_inventory_finalize_rejects_incomplete_bound_member_history():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_history_incomplete_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            incomplete = _build_0048_inventory(
                connection,
                authority,
                finalize=False,
                include_history_page=False,
            )
            assert _postgres_statement_rejected(
                connection,
                "history|page|complete|watermark",
                lambda: connection.execute(
                    "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
                    (incomplete["inventory_id"],),
                ),
            )
            connection.execute(
                "UPDATE job_schedules SET status='paused' WHERE id=%s",
                (incomplete["facts"]["schedule_id"],),
            )
            wrong_last = _build_0048_inventory(
                connection, authority, finalize=False
            )
            page = connection.execute(
                """SELECT remote_schedule_id,page_index,request_cursor_sha256,
                          response_sha256,item_count,next_cursor_sha256,is_terminal,
                          item_set_sha256
                   FROM v4_production_schedule_inventory_pages
                   WHERE inventory_id=%s AND remote_schedule_id IS NOT NULL""",
                (wrong_last["inventory_id"],),
            ).fetchone()
            wrong_last_digest = "0" * 64
            wrong_receipt = _production_schedule_sha256_jsonb(
                connection,
                {
                    "inventory_id": str(wrong_last["inventory_id"]),
                    "remote_schedule_id": page[0],
                    "page_index": page[1],
                    "request_cursor_sha256": page[2],
                    "response_sha256": page[3],
                    "item_count": page[4],
                    "next_cursor_sha256": page[5],
                    "is_terminal": page[6],
                    "item_set_sha256": page[7],
                    "last_item_sha256": wrong_last_digest,
                },
            )
            assert _postgres_statement_rejected(
                connection,
                "last item digest",
                lambda: connection.execute(
                    """UPDATE v4_production_schedule_inventory_pages
                       SET last_item_sha256=%s,page_receipt_sha256=%s
                       WHERE inventory_id=%s AND remote_schedule_id IS NOT NULL""",
                    (wrong_last_digest, wrong_receipt, wrong_last["inventory_id"]),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_source_kind_schedule_id_publication_schedule_id_matrix():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_source_matrix_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            adopted = _build_0048_inventory(
                connection, authority, finalize=False
            )
            bad_snapshot = dict(adopted["member_snapshot"])
            bad_snapshot["source_kind"] = "governed_publication"
            bad_snapshot["publication_schedule_id"] = None
            bad_snapshot["schedule_id"] = str(uuid4())
            bad_snapshot["remote_schedule_id"] = "publication:" + uuid4().hex
            bad_sha = _production_schedule_sha256_jsonb(connection, bad_snapshot)
            assert _postgres_statement_rejected(
                connection,
                "source|matrix|publication|member",
                lambda: connection.execute(
                    """INSERT INTO v4_production_schedule_inventory_members(
                              inventory_id,remote_schedule_id,page_index,classification,
                              member_snapshot,member_sha256,history_page_count,
                              history_action_count,history_page_chain_sha256,
                              history_action_set_sha256)
                       VALUES(%s,%s,0,'bound',%s,%s,1,0,%s,%s)""",
                    (
                        adopted["inventory_id"],
                        bad_snapshot["remote_schedule_id"],
                        Jsonb(bad_snapshot),
                        bad_sha,
                        adopted["member_snapshot"]["history_page_chain_sha256"],
                        adopted["member_snapshot"]["history_action_set_sha256"],
                    ),
                ),
            )
            connection.execute(
                "UPDATE job_schedules SET status='paused' WHERE id=%s",
                (adopted["facts"]["schedule_id"],),
            )
            publication = _build_0048_inventory(
                connection,
                authority,
                source_kind="governed_publication",
                finalize=True,
            )
            assert publication["member_snapshot"]["publication_schedule_id"]
            assert publication["member_snapshot"]["schedule_id"]
    finally:
        _drop_migration_database(source, name)


def test_0048_binding_requires_exact_member_and_schedule_approval():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_exact_binding_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            assert _postgres_statement_rejected(
                connection,
                "member|binding|exact|inventory",
                lambda: _build_0048_inventory(
                    connection,
                    authority,
                    finalize=False,
                    binding_snapshot_overrides={"task_queue": "wrong-queue"},
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "approval|binding|inventory|digest|member",
                lambda: _build_0048_inventory(
                    connection,
                    authority,
                    finalize=False,
                    approval_context_overrides={"inventory_sha256": "0" * 64},
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_deferred_finalization_requires_one_binding_per_bound_member():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_deferred_binding_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            inventory = _build_0048_inventory(
                connection, authority, include_binding=False, finalize=False
            )
            assert _postgres_statement_rejected(
                connection,
                "binding|bound member|complete|inventory",
                lambda: connection.execute(
                    "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
                    (inventory["inventory_id"],),
                ),
            )
            assert connection.execute(
                "SELECT finalized_at FROM v4_production_schedule_inventories WHERE inventory_id=%s",
                (inventory["inventory_id"],),
            ).fetchone()[0] is None
            connection.commit()
            try:
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('salience.p1_production_schedule_finalize',%s,true)",
                        (str(inventory["inventory_id"]),),
                    )
                    connection.execute(
                        """UPDATE v4_production_schedule_inventories
                           SET finalized_at=clock_timestamp() WHERE inventory_id=%s""",
                        (inventory["inventory_id"],),
                    )
            except psycopg.Error as error:
                assert re.search(
                    "binding|bound member|complete|inventory", str(error)
                ), str(error)
            else:
                raise AssertionError(
                    "deferred schedule inventory validation accepted a missing binding"
                )
            assert connection.execute(
                "SELECT finalized_at FROM v4_production_schedule_inventories WHERE inventory_id=%s",
                (inventory["inventory_id"],),
            ).fetchone()[0] is None
    finally:
        _drop_migration_database(source, name)


def test_0048_out_of_scope_member_requires_owner_and_reason():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_out_of_scope_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            inventory = _build_0048_inventory(
                connection,
                authority,
                classification="out_of_scope",
                include_binding=False,
                finalize=False,
            )
            bad_snapshot = dict(inventory["member_snapshot"])
            bad_snapshot["owner_ref"] = ""
            bad_snapshot["exclusion_reason"] = ""
            bad_snapshot_sha = _production_schedule_sha256_jsonb(
                connection, bad_snapshot
            )
            assert _postgres_statement_rejected(
                connection,
                "owner|reason|out.of.scope|member",
                lambda: connection.execute(
                    """INSERT INTO v4_production_schedule_inventory_members(
                              inventory_id,remote_schedule_id,page_index,classification,
                              member_snapshot,member_sha256,history_page_count,
                              history_action_count,history_page_chain_sha256,
                              history_action_set_sha256)
                       VALUES(%s,%s,1,'out_of_scope',%s,%s,1,0,%s,%s)""",
                    (
                        inventory["inventory_id"],
                        "out-of-scope-invalid:" + uuid4().hex,
                        Jsonb(bad_snapshot),
                        bad_snapshot_sha,
                        bad_snapshot["history_page_chain_sha256"],
                        bad_snapshot["history_action_set_sha256"],
                    ),
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_schedule_approval_shape_and_decided_identity_are_immutable():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_approval_immutable_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            adopted = _build_0048_inventory(connection, authority)
            row = connection.execute(
                """SELECT id,request_context FROM approval_requests
                   WHERE id=%s""",
                (adopted["approval_id"],),
            ).fetchone()
            assert set(row[1]) == set(_PRODUCTION_SCHEDULE_APPROVAL_KEYS)
            assert _postgres_statement_rejected(
                connection,
                "decided|immutable|approval",
                lambda: connection.execute(
                    "UPDATE approval_requests SET decided_by='changed' WHERE id=%s",
                    (row[0],),
                ),
            )
            assert _postgres_statement_rejected(
                connection,
                "decided|immutable|approval",
                lambda: connection.execute(
                    "DELETE FROM approval_requests WHERE id=%s", (row[0],)
                ),
            )
    finally:
        _drop_migration_database(source, name)


def test_0048_untyped_approval_missing_or_null_schema_version_is_ignored():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_untyped_approval_tag_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            adopted = _build_0048_inventory(
                connection, authority, include_binding=False, finalize=False
            )
            base = connection.execute(
                """SELECT workspace_id,policy_version_id,request_context,requested_by
                   FROM approval_requests WHERE id=%s""",
                (adopted["approval_id"],),
            ).fetchone()
            for tag_state in ("absent", "json-null"):
                for effect_type, should_reject in (
                    ("creative_distribution", False),
                    ("production_schedule_binding", True),
                ):
                    context = dict(base[2])
                    if tag_state == "absent":
                        context.pop("schema_version")
                    else:
                        context["schema_version"] = None
                    rejected = _postgres_statement_rejected(
                        connection,
                        "production schedule approval",
                        lambda: connection.execute(
                            """INSERT INTO approval_requests(
                                      id,workspace_id,policy_version_id,effect_type,status,
                                      request_context,requested_by)
                               VALUES(%s,%s,%s,%s,'pending',%s,%s)""",
                            (
                                uuid4(),
                                base[0],
                                base[1],
                                effect_type,
                                Jsonb(context),
                                base[3],
                            ),
                        ),
                    )
                    assert rejected is should_reject, (
                        f"{effect_type} with {tag_state} schema_version: "
                        f"expected rejection={should_reject}, got {rejected}"
                    )
    finally:
        _drop_migration_database(source, name)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("remote_state", "running"),
        ("next_action", "2099-01-01T00:02:00+00:00"),
        ("running_action_count", 1),
    ),
    ids=("remote-state", "next-action", "running-count"),
)
def test_0048_binding_runtime_facts_match_member_in_insert_guard(field, value):
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_runtime_guard_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            assert _postgres_statement_rejected(
                connection,
                "binding facts|match its member",
                lambda: _build_0048_inventory(
                    connection,
                    authority,
                    finalize=False,
                    binding_fact_overrides={field: value},
                ),
            ), f"binding insert guard accepted mismatched {field}"
    finally:
        _drop_migration_database(source, name)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("remote_state", "running"),
        ("next_action", "2099-01-01T00:02:00+00:00"),
        ("running_action_count", 1),
    ),
    ids=("remote-state", "next-action", "running-count"),
)
def test_0048_binding_runtime_facts_match_member_in_finalizer(field, value):
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_runtime_finalizer_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            inventory = _build_0048_inventory(
                connection,
                authority,
                finalize=False,
                binding_fact_overrides={field: value},
                bypass_binding_guard=True,
            )
            assert _postgres_statement_rejected(
                connection,
                "binding facts|fingerprint|member",
                lambda: connection.execute(
                    "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
                    (inventory["inventory_id"],),
                ),
            ), f"inventory finalizer accepted mismatched {field}"
    finally:
        _drop_migration_database(source, name)


def test_0048_schedule_approval_rejects_null_or_non_string_required_fields():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_approval_json_types_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            adopted = _build_0048_inventory(
                connection, authority, include_binding=False, finalize=False
            )
            base = connection.execute(
                """SELECT workspace_id,policy_version_id,effect_type,
                          request_context,requested_by
                   FROM approval_requests WHERE id=%s""",
                (adopted["approval_id"],),
            ).fetchone()
            nullable_fields = {"publication_schedule_id", "history_watermark_sha256"}
            numeric_fields = {"goal_revision", "fencing_epoch"}
            rejected_cases = []
            for field in _PRODUCTION_SCHEDULE_APPROVAL_KEYS:
                invalid_values = ["123"] if field in numeric_fields else [123]
                if field not in nullable_fields:
                    invalid_values.insert(0, None)
                for invalid_value in invalid_values:
                    context = dict(base[3])
                    context[field] = invalid_value
                    rejected = _postgres_statement_rejected(
                        connection,
                        "production schedule approval|context|identity|malformed|invalid input",
                        lambda context=context: connection.execute(
                            """INSERT INTO approval_requests(
                                      id,workspace_id,policy_version_id,effect_type,status,
                                      request_context,requested_by,decided_by,decided_at)
                               VALUES(%s,%s,%s,%s,'approved',%s,%s,
                                      'schedule-reviewer',clock_timestamp())""",
                            (
                                uuid4(),
                                base[0],
                                base[1],
                                base[2],
                                Jsonb(context),
                                base[4],
                            ),
                        ),
                    )
                    rejected_cases.append((field, invalid_value, rejected))
            accepted = [
                (field, value)
                for field, value, rejected in rejected_cases
                if not rejected
            ]
            assert not accepted, f"approval guard accepted invalid context values: {accepted}"
    finally:
        _drop_migration_database(source, name)


def test_0048_downgrade_refuses_adopted_schedule_evidence():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = _prepare_0048_database(
        "p1_0048_populated_downgrade_"
    )
    try:
        _upgrade_0048_or_fail(database)
        with psycopg.connect(database) as connection:
            adopted = _build_0048_inventory(connection, authority)
            before = connection.execute(
                """SELECT
                     (SELECT count(*) FROM v4_production_schedule_inventories),
                     (SELECT count(*) FROM v4_production_schedule_inventory_pages),
                     (SELECT count(*) FROM v4_production_schedule_inventory_members),
                     (SELECT count(*) FROM v4_production_schedule_source_bindings),
                     (SELECT to_jsonb(i) FROM v4_production_schedule_inventories i
                       WHERE inventory_id=%s),
                     (SELECT to_jsonb(b) FROM v4_production_schedule_source_bindings b
                       WHERE inventory_id=%s),
                     (SELECT version_num FROM alembic_version)""",
                (adopted["inventory_id"], adopted["inventory_id"]),
            ).fetchone()
        downgrade = _migration(database, "downgrade", _REVISION_0047)
        assert downgrade.returncode != 0
        assert "preserve production schedule inventory and binding evidence" in (
            downgrade.stdout + downgrade.stderr
        )
        with psycopg.connect(database) as connection:
            after = connection.execute(
                """SELECT
                     (SELECT count(*) FROM v4_production_schedule_inventories),
                     (SELECT count(*) FROM v4_production_schedule_inventory_pages),
                     (SELECT count(*) FROM v4_production_schedule_inventory_members),
                     (SELECT count(*) FROM v4_production_schedule_source_bindings),
                     (SELECT to_jsonb(i) FROM v4_production_schedule_inventories i
                       WHERE inventory_id=%s),
                     (SELECT to_jsonb(b) FROM v4_production_schedule_source_bindings b
                       WHERE inventory_id=%s),
                     (SELECT version_num FROM alembic_version)""",
                (adopted["inventory_id"], adopted["inventory_id"]),
            ).fetchone()
            assert after == before
    finally:
        _drop_migration_database(source, name)


def test_0048_empty_downgrade_restores_0047_head():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, fixture_contract, _, _ = _prepare_0048_database(
        "p1_0048_empty_downgrade_"
    )
    try:
        _upgrade_0048_or_fail(database)
        downgraded = _migration(database, "downgrade", _REVISION_0047)
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0047
            _assert_0043_fixture_contract(connection, fixture_contract)
            for relation in (
                "v4_production_schedule_inventories",
                "v4_production_schedule_inventory_pages",
                "v4_production_schedule_inventory_members",
                "v4_production_schedule_source_bindings",
            ):
                assert connection.execute(
                    "SELECT to_regclass(%s)", ("public." + relation,)
                ).fetchone()[0] is None
            for function in (
                "v4_finalize_production_schedule_inventory_v1(uuid)",
                "v4_validate_production_schedule_inventory_v1(uuid)",
                "v4_guard_production_schedule_approval_v1()",
            ):
                assert connection.execute(
                    "SELECT to_regprocedure(%s)", ("public." + function,)
                ).fetchone()[0] is None
    finally:
        _drop_migration_database(source, name)


_REVISION_0049 = "0049_p1_production_recovery_decision_evidence"
_REVISION_0050 = "0050_p1_production_dispatch_reissue"


def _prepare_0049_database(prefix):
    source = os.environ["TEST_DATABASE_URL"]
    name, database, fixture_contract, fixture_state, authority = _prepare_0048_database(
        prefix
    )
    try:
        _upgrade_0048_or_fail(database)
        upgraded = _migration(database, "upgrade", _REVISION_0049)
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        return name, database, fixture_contract, fixture_state, authority
    except Exception:
        _drop_migration_database(source, name)
        raise


def _upgrade_0050_or_fail(database):
    upgraded = _migration(database, "upgrade", _REVISION_0050)
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    return upgraded


def _capture_0050_predecessor_history(connection):
    return {
        "permits": connection.execute(
            """SELECT jsonb_agg(to_jsonb(permit)-'recovery_command_id' ORDER BY permit_id)
               FROM v4_production_effect_permits permit"""
        ).fetchone()[0],
        "claims": connection.execute(
            """SELECT jsonb_agg(to_jsonb(claim) ORDER BY claim_id)
               FROM v4_production_effect_permit_claims claim"""
        ).fetchone()[0],
        "dispatch": connection.execute(
            """SELECT jsonb_agg(to_jsonb(dispatch) ORDER BY operation_id)
               FROM v4_production_current_dispatch dispatch"""
        ).fetchone()[0],
        "recovery_work": connection.execute(
            """SELECT jsonb_agg(to_jsonb(work) ORDER BY work_id)
               FROM v4_production_recovery_work work"""
        ).fetchone()[0],
        "recovery_events": connection.execute(
            """SELECT jsonb_agg(to_jsonb(event) ORDER BY event_id)
               FROM v4_production_recovery_events event"""
        ).fetchone()[0],
        "permit_snapshots": connection.execute(
            """SELECT jsonb_agg(jsonb_build_array(permit_id,request_snapshot)
                                      ORDER BY permit_id)
               FROM v4_production_effect_permits"""
        ).fetchone()[0],
    }


def _capture_0050_predecessor_dispatch_contract(connection):
    return {
        "permit_validator": connection.execute(
            "SELECT pg_get_functiondef('public.v4_validate_production_effect_permit_v1()'::regprocedure)"
        ).fetchone()[0],
        "dispatch_advance": connection.execute(
            """SELECT pg_get_functiondef(
                   'public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)'::regprocedure
               )"""
        ).fetchone()[0],
        "snapshot_constraint": connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='public.v4_production_effect_permits'::regclass "
            "AND conname='ck_v4_production_effect_permits_snapshot'"
        ).fetchone()[0],
        "validator_trigger": connection.execute(
            """SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger
               WHERE tgrelid='public.v4_production_effect_permits'::regclass
                 AND tgname='validate_v4_production_effect_permit_v1'"""
        ).fetchone(),
        "recovery_event_validator": connection.execute(
            "SELECT pg_get_functiondef('public.v4_validate_production_recovery_event_v2()'::regprocedure)"
        ).fetchone()[0],
        "recovery_event_trigger": connection.execute(
            """SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger
               WHERE tgrelid='public.v4_production_recovery_events'::regclass
                 AND tgname='validate_v4_production_recovery_event_v2'"""
        ).fetchone(),
    }


def test_0050_preserves_all_predecessor_history():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _fixture_contract, _fixture_state, _authority = (
        _prepare_0049_database("p1_0050_history_preserved_")
    )
    try:
        with psycopg.connect(database) as connection:
            before = _capture_0050_predecessor_history(connection)
            assert before["permits"]
            assert all(
                row[1]["schema_version"] == "salience.p1-production-effect-permit.v1"
                for row in before["permit_snapshots"]
            )
        _upgrade_0050_or_fail(database)
        with psycopg.connect(database) as connection:
            after = _capture_0050_predecessor_history(connection)
            assert after == before
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0050
            assert connection.execute(
                "SELECT count(*) FROM v4_production_effect_permits "
                "WHERE request_snapshot->>'schema_version'="
                "'salience.p1-production-effect-permit.v1'"
            ).fetchone()[0] == len(before["permit_snapshots"])
            recovery_validator = connection.execute(
                "SELECT pg_get_functiondef('public.v4_validate_production_recovery_event_v3()'::regprocedure)"
            ).fetchone()[0]
            assert "observation_event_id" in recovery_validator
            assert "observation_sha256" in recovery_validator
            assert "FOR UPDATE;" in recovery_validator
            assert connection.execute(
                """SELECT tgname FROM pg_trigger
                   WHERE tgrelid='public.v4_production_recovery_events'::regclass
                     AND tgname='validate_v4_production_recovery_event_v3'"""
            ).fetchone()[0] == "validate_v4_production_recovery_event_v3"
            dispatch_advance = connection.execute(
                """SELECT pg_get_functiondef(
                       'public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)'::regprocedure
                   )"""
            ).fetchone()[0]
            cycle_lock = dispatch_advance.index("FOR SHARE OF cycle")
            case_lock = dispatch_advance.index("FOR SHARE OF recovery")
            pointer_advance = dispatch_advance.index(
                "UPDATE public.v4_production_current_dispatch AS dispatch"
            )
            assert cycle_lock < case_lock < pointer_advance
            assert "observation_event_id" in dispatch_advance
            assert "observation_sha256" in dispatch_advance
            assert "occurred_at >= recovery_observation_row.occurred_at" in dispatch_advance
    finally:
        _drop_migration_database(source, name)


def test_0050_populated_downgrade_refuses_without_mutation():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _fixture_contract, _fixture_state, _authority = (
        _prepare_0049_database("p1_0050_populated_downgrade_")
    )
    try:
        _upgrade_0050_or_fail(database)
        with psycopg.connect(database) as connection:
            connection.execute("SET LOCAL session_replication_role='replica'")
            connection.execute(
                """UPDATE v4_production_effect_permits
                       SET request_snapshot=jsonb_set(
                           jsonb_set(
                             jsonb_set(request_snapshot,'{schema_version}',
                               to_jsonb('salience.p1-production-effect-permit.v2'::text)),
                             '{route_tuple_sha256}',to_jsonb(repeat('a',64))),
                           '{recovery_command_id}','null'::jsonb)"""
            )
            before = connection.execute(
                """SELECT
                     (SELECT jsonb_agg(to_jsonb(permit) ORDER BY permit_id)
                      FROM v4_production_effect_permits permit),
                     (SELECT jsonb_agg(to_jsonb(dispatch) ORDER BY operation_id)
                      FROM v4_production_current_dispatch dispatch),
                     (SELECT version_num FROM alembic_version),
                     to_regclass('public.uq_v4_production_effect_permits_recovery_command'),
                     to_regclass('public.v4_production_effect_permits')"""
            ).fetchone()
        downgrade = _migration(database, "downgrade", _REVISION_0049)
        assert downgrade.returncode != 0
        assert "preserve production permit reissue history" in (
            downgrade.stdout + downgrade.stderr
        )
        with psycopg.connect(database) as connection:
            after = connection.execute(
                """SELECT
                     (SELECT jsonb_agg(to_jsonb(permit) ORDER BY permit_id)
                      FROM v4_production_effect_permits permit),
                     (SELECT jsonb_agg(to_jsonb(dispatch) ORDER BY operation_id)
                      FROM v4_production_current_dispatch dispatch),
                     (SELECT version_num FROM alembic_version),
                     to_regclass('public.uq_v4_production_effect_permits_recovery_command'),
                     to_regclass('public.v4_production_effect_permits')"""
            ).fetchone()
            assert after == before
    finally:
        _drop_migration_database(source, name)


def test_0050_empty_downgrade_restores_0049_contract():
    source = os.environ["TEST_DATABASE_URL"]
    name, database, fixture_contract, _fixture_state, _authority = (
        _prepare_0049_database("p1_0050_empty_downgrade_")
    )
    try:
        with psycopg.connect(database) as connection:
            predecessor_contract = _capture_0050_predecessor_dispatch_contract(
                connection
            )
        _upgrade_0050_or_fail(database)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_effect_permits "
                "WHERE request_snapshot->>'schema_version'="
                "'salience.p1-production-effect-permit.v2'"
            ).fetchone()[0] == 0
        downgraded = _migration(database, "downgrade", _REVISION_0049)
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == _REVISION_0049
            assert connection.execute(
                "SELECT to_regclass('public.v4_production_effect_permits') IS NOT NULL"
            ).fetchone()[0]
            assert connection.execute(
                "SELECT to_regclass('public.uq_v4_production_effect_permits_recovery_command')"
            ).fetchone()[0] is None
            assert connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='v4_production_effect_permits' "
                "AND column_name='recovery_command_id'"
            ).fetchone() is None
            assert connection.execute(
                "SELECT request_snapshot->>'schema_version' "
                "FROM v4_production_effect_permits"
            ).fetchone()[0] == "salience.p1-production-effect-permit.v1"
            assert _capture_0050_predecessor_dispatch_contract(
                connection
            ) == predecessor_contract
            assert connection.execute(
                "SELECT to_regprocedure('public.v4_validate_production_recovery_event_v3()')"
            ).fetchone()[0] is None
    finally:
        _drop_migration_database(source, name)
