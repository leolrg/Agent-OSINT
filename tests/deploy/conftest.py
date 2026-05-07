"""Shared fixtures for deployment tests.

`pg_url`: spins up a transient Postgres in Docker, applies the
Drizzle SQL migration, yields a SQLAlchemy URL, then tears down.
Tests using it must be marked `@pytest.mark.integration` so unit
runs (`pytest -m 'not integration'`) skip them.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SQL = REPO_ROOT / "web-next" / "drizzle" / "migrations" / "0000_initial.sql"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def pg_url():
    port = _free_port()
    name = f"agent-osint-test-pg-{port}"
    subprocess.run([
        "docker", "run", "-d", "--rm", "--name", name,
        "-e", "POSTGRES_USER=app", "-e", "POSTGRES_PASSWORD=app",
        "-e", "POSTGRES_DB=agent_osint",
        "-p", f"{port}:5432",
        "postgres:16",
    ], check=True, capture_output=True)
    url = f"postgresql+psycopg://app:app@localhost:{port}/agent_osint"
    try:
        # Wait for readiness.
        engine = create_engine(url)
        for _ in range(30):
            try:
                with engine.connect() as c:
                    c.execute(text("SELECT 1"))
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("postgres did not become ready in 15s")
        # Apply migration.
        sql = MIGRATION_SQL.read_text()
        with engine.begin() as c:
            for stmt in [s for s in sql.split(";") if s.strip()]:
                c.execute(text(stmt))
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


@pytest.fixture(autouse=True)
def _cleanup_db(request, pg_url):
    """Truncate app tables between integration tests so they don't share state.

    Only runs for tests marked `integration` (the marker is the gate that
    selects which tests use the shared Postgres). Cascading from `users`
    cleans up `sessions`, `scans`, and `scan_runs`.
    """
    if "integration" not in {m.name for m in request.node.iter_markers()}:
        yield
        return
    yield
    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.execute(text(
            "TRUNCATE users, allowed_emails RESTART IDENTITY CASCADE"
        ))
