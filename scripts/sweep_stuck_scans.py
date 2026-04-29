"""Mark scans stuck in `running` longer than the threshold as failed.

Designed to run on a 10-minute cron in production. Idempotent —
running twice in a row is a no-op the second time.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from osint.db.models import Scan
from osint.db.session import db_session


def sweep(*, threshold_minutes: int = 90, redis_url: str | None = None) -> int:
    """Returns count of scans marked failed."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    n = 0
    with db_session() as s:
        rows = s.execute(
            select(Scan).where(Scan.status == "running", Scan.started_at < cutoff)
        ).scalars().all()
        for sc in rows:
            sc.status = "failed"
            sc.error_message = "worker_timeout"
            sc.completed_at = datetime.now(timezone.utc)
            n += 1

    if n and (redis_url or os.environ.get("REDIS_URL")):
        # Best-effort terminal event so any open SSE clients close.
        import redis as _redis  # noqa: WPS433
        try:
            r = _redis.from_url(redis_url or os.environ["REDIS_URL"])
            for sc in rows:
                payload = json.dumps({"ts": time.time(), "level": "info",
                                      "event": "scan.failed",
                                      "scan_id": str(sc.id),
                                      "error": "worker_timeout"})
                r.publish(f"scan:{sc.id}", payload)
        except Exception:
            pass

    return n


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--threshold-minutes", type=int, default=90)
    args = p.parse_args()
    n = sweep(threshold_minutes=args.threshold_minutes)
    print(f"swept {n} stuck scans")
    sys.exit(0)
