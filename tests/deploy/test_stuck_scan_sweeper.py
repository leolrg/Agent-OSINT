"""Sweeper marks scans stuck in `running` for >threshold as `failed`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from osint.db.models import Scan, User


pytestmark = pytest.mark.integration


def test_sweeper_marks_only_stuck_scans(pg_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)

    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush()
        now = datetime.now(timezone.utc)
        # Stuck (>90 min in running)
        s.add(Scan(user_id=u.id, status="running",
                   started_at=now - timedelta(minutes=120),
                   agent="x", params={}))
        # Fresh (still legitimately running)
        s.add(Scan(user_id=u.id, status="running",
                   started_at=now - timedelta(minutes=5),
                   agent="x", params={}))
        # Already-completed (must NOT be touched)
        s.add(Scan(user_id=u.id, status="completed",
                   started_at=now - timedelta(minutes=200),
                   completed_at=now - timedelta(minutes=10),
                   agent="x", params={}))
        s.commit()

    from scripts.sweep_stuck_scans import sweep
    n = sweep(threshold_minutes=90)
    assert n == 1

    with Sess() as s:
        rows = s.query(Scan).order_by(Scan.started_at).all()
        # ORDER BY started_at ASC: oldest (-200 min, completed) first,
        # then stuck (-120 min, now failed), then fresh (-5 min, running).
        assert rows[0].status == "completed"  # oldest, untouched
        assert rows[1].status == "failed"     # stuck → swept
        assert rows[1].error_message == "worker_timeout"
        assert rows[2].status == "running"    # fresh, untouched
