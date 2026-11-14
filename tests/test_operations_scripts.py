from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_postgres_backup_script_creates_and_verifies_archive(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
printf '%s\n' "$*" >>"$DOCKER_CALL_LOG"
case "$*" in
  *pg_dump*)
    printf 'fake custom-format backup\n'
    ;;
  *pg_restore*)
    cat >/dev/null
    ;;
  *)
    exit 64
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    backup_dir = tmp_path / "backups"
    docker_call_log = tmp_path / "docker-calls.log"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "BACKUP_DIR": str(backup_dir),
        "BOTERA_PRODUCTION_ENV_FILE": ".env.production.example",
        "DOCKER_CALL_LOG": str(docker_call_log),
    }

    result = subprocess.run(
        [REPOSITORY_ROOT / "scripts" / "backup_postgres.sh", "prod"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    backups = tuple(backup_dir.glob("botera_master_*.dump"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "fake custom-format backup\n"
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert "created and verified" in result.stdout
    calls = docker_call_log.read_text(encoding="utf-8")
    assert "--project-name botera-master-production" in calls
    assert "pg_dump" in calls
    assert "pg_restore --list" in calls


def test_postgres_backup_verifier_rejects_empty_archive(tmp_path: Path) -> None:
    empty_backup = tmp_path / "empty.dump"
    empty_backup.touch()

    result = subprocess.run(
        [
            REPOSITORY_ROOT / "scripts" / "verify_postgres_backup.sh",
            "dev",
            empty_backup,
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "does not exist or is empty" in result.stderr


def test_postgres_backup_script_requires_explicit_mode() -> None:
    result = subprocess.run(
        [REPOSITORY_ROOT / "scripts" / "backup_postgres.sh"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_postgres_restore_rehearsal_uses_only_isolated_database(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
printf '%s\n' "$*" >>"$DOCKER_CALL_LOG"
case "$*" in
  *pg_restore*|*dropdb*|*createdb*|*psql*)
    cat >/dev/null || true
    ;;
  *)
    exit 64
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    backup_path = tmp_path / "pilot.dump"
    backup_path.write_bytes(b"fake custom-format backup\n")
    docker_call_log = tmp_path / "docker-calls.log"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "BOTERA_PRODUCTION_ENV_FILE": ".env.production.example",
        "DOCKER_CALL_LOG": str(docker_call_log),
    }

    result = subprocess.run(
        [
            REPOSITORY_ROOT / "scripts" / "rehearse_postgres_restore.sh",
            "prod",
            backup_path,
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    calls = docker_call_log.read_text(encoding="utf-8")
    assert "--project-name botera-master-production" in calls
    assert "createdb" in calls
    assert "pg_restore --exit-on-error" in calls
    assert "SELECT COUNT(*) FROM django_migrations" in calls
    database_commands = [
        line
        for line in calls.splitlines()
        if any(command in line for command in ("dropdb", "createdb", "psql"))
        or "pg_restore --exit-on-error" in line
    ]
    assert database_commands
    assert all("botera_master_restore_check" in line for line in database_commands)
    assert "restore rehearsal succeeded" in result.stdout
