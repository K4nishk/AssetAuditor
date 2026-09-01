"""Ephemeral local Postgres cluster for migration/RLS tests.

Real Postgres, not sqlite — RLS, jsonb, and uuid semantics don't translate.
Skips cleanly wherever initdb/pg_ctl aren't installed, per CLAUDE.md: prove
what's provable locally against PostgreSQL, never fake a passing test.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

PG_BINARIES = ["initdb", "pg_ctl", "createdb", "psql"]


def _pg_available() -> bool:
    return all(shutil.which(binary) for binary in PG_BINARIES)


@pytest.fixture(scope="session")
def pg_cluster():
    if not _pg_available():
        pytest.skip(
            "initdb/pg_ctl/createdb not on PATH — cannot verify migrations against a live Postgres"
        )

    base = Path(tempfile.mkdtemp(prefix="aa-pg-", dir=os.environ.get("TMPDIR")))
    data_dir = base / "data"
    log_path = base / "server.log"

    init = subprocess.run(
        [
            "initdb",
            "--username=postgres",
            "--auth=trust",
            "--no-sync",
            "--locale=C",
            # The sandbox blocks SysV shmget(); mmap-based shared memory avoids it.
            "-c",
            "shared_memory_type=mmap",
            "-c",
            "dynamic_shared_memory_type=posix",
            "-D",
            str(data_dir),
        ],
        capture_output=True,
        text=True,
    )
    if init.returncode != 0:
        pytest.skip(f"initdb failed: {init.stderr}")

    start = subprocess.run(
        [
            "pg_ctl",
            "start",
            "-D",
            str(data_dir),
            "-l",
            str(log_path),
            "-w",
            "-o",
            f"-c listen_addresses='' -c unix_socket_directories='{base}'",
        ],
        capture_output=True,
        text=True,
    )
    if start.returncode != 0:
        log = log_path.read_text() if log_path.exists() else ""
        pytest.skip(f"pg_ctl start failed: {start.stderr}\n{log}")

    try:
        created = subprocess.run(
            ["createdb", "-h", str(base), "-U", "postgres", "assetauditor_test"],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            pytest.skip(f"createdb failed: {created.stderr}")

        yield {"socket_dir": str(base), "dbname": "assetauditor_test", "admin_user": "postgres"}
    finally:
        subprocess.run(
            ["pg_ctl", "stop", "-D", str(data_dir), "-m", "immediate"],
            capture_output=True,
        )
        shutil.rmtree(base, ignore_errors=True)
