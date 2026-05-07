"""GET /api/stream/scans/{scan_id} — SSE stream of agent events.

Verifies auth + ownership, replays Redis history list oldest-first,
then SUBSCRIBEs and forwards live events. Closes on terminal events
(scan.completed / scan.failed). Uses sse-starlette's EventSourceResponse.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import AsyncIterator

import redis as _redis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from osint.api.dependencies import User, current_user
from osint.db.models import Scan


router = APIRouter()

TERMINAL_EVENTS = {"scan.completed", "scan.failed"}


def _redis_client() -> _redis.Redis:
    """Override target for tests (fakeredis injection)."""
    return _redis.from_url(os.environ["REDIS_URL"])


@router.get("/api/stream/scans/{scan_id}")
async def stream_scan(
    scan_id: uuid.UUID,
    user: User = Depends(current_user),
):
    # Lazy import (Task 7 caveat): osint.db.session evaluates DATABASE_URL at module import.
    from osint.db.session import db_session

    # Auth + ownership.
    with db_session() as s:
        sc = s.execute(select(Scan).where(Scan.id == scan_id)).scalar_one_or_none()
        if sc is None or str(sc.user_id) != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    rds = _redis_client()
    channel = f"scan:{scan_id}"
    history_key = f"scan:{scan_id}:events"

    async def gen() -> AsyncIterator[dict]:
        # Replay history oldest-first. LPUSH stores newest-first, so reverse.
        history = rds.lrange(history_key, 0, -1)
        for raw in reversed(history):
            data = raw.decode() if isinstance(raw, bytes) else raw
            yield {"data": data}
            try:
                e = json.loads(data)
                if e.get("event") in TERMINAL_EVENTS:
                    return  # already terminal; no need to subscribe
            except json.JSONDecodeError:
                pass

        # Subscribe for live events.
        pubsub = rds.pubsub()
        pubsub.subscribe(channel)
        try:
            for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                yield {"data": data}
                try:
                    e = json.loads(data)
                    if e.get("event") in TERMINAL_EVENTS:
                        return
                except json.JSONDecodeError:
                    pass
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()

    return EventSourceResponse(gen(), ping=15)
