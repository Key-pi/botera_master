from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_postgres_backup_script_creates_and_verifies_archive(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
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
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "BACKUP_DIR": str(backup_dir),
    }

    result = subprocess.run(
        [REPOSITORY_ROOT / "scripts" / "backup_postgres.sh"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    backups = tuple(backup_dir.glob("botera_master_*.dump"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "fake custom-format backup\n"
    assert "created and verified" in result.stdout


def test_postgres_backup_verifier_rejects_empty_archive(tmp_path: Path) -> None:
    empty_backup = tmp_path / "empty.dump"
    empty_backup.touch()

    result = subprocess.run(
        [
            REPOSITORY_ROOT / "scripts" / "verify_postgres_backup.sh",
            empty_backup,
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "does not exist or is empty" in result.stderr
