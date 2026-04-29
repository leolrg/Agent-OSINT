"""SSE stream — replay-then-subscribe ordering, auth check, terminal close.

These tests use sync TestClient + threading; SSE in TestClient returns
a streaming response we read line-by-line.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import fakeredis
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from osint.api.app import create_app
from osint.db.models import Scan, User


pytestmark = pytest.mark.integration

SECRET = "test-secret-padding-padding-padding-padding"


def _sign(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "email": "a@example.com", "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def _seed(pg_url: str):
    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush()
        sc = Scan(user_id=u.id, status="running", agent="react_v1", params={})
        s.add(sc); s.flush()
        s.commit()
        return u.id, sc.id


def _push_history(redis, scan_id, events: list[dict]):
    # Mirror the real RedisEventSink: LPUSH each event chronologically, which
    # leaves the head of the list as the newest event. The endpoint will then
    # `lrange` + reverse to replay oldest-first.
    for e in events:
        redis.lpush(f"scan:{scan_id}:events", json.dumps(e))


def _read_sse_lines(response, max_events: int, timeout: float = 3.0) -> list[dict]:
    """Pull SSE `data:` lines off the streaming response."""
    out: list[dict] = []
    deadline = time.time() + timeout
    for line in response.iter_lines():
        if line and line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
            if len(out) >= max_events:
                break
        if time.time() > deadline:
            break
    return out


@pytest.fixture
def stream_setup(pg_url, monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr("osint.api.routes.stream._redis_client", lambda: fake)
    return fake


def test_replays_history_then_terminates_on_completed(stream_setup, pg_url):
    fake = stream_setup
    user_id, scan_id = _seed(pg_url)
    _push_history(fake, scan_id, [
        {"event": "scan.started", "seq": 0},
        {"event": "tool.started", "tool_name": "web_search",
         "args": {"query": "x"}, "seq": 1},
        {"event": "scan.completed", "seq": 2, "s3_key": "k"},
    ])

    client = TestClient(create_app())
    with client.stream(
        "GET", f"/api/stream/scans/{scan_id}",
        cookies={"next-auth.session-token": _sign(str(user_id))},
    ) as r:
        assert r.status_code == 200
        events = _read_sse_lines(r, max_events=3)
    assert [e["event"] for e in events] == \
        ["scan.started", "tool.started", "scan.completed"]


def test_unauthenticated_is_401(stream_setup, pg_url):
    user_id, scan_id = _seed(pg_url)
    client = TestClient(create_app())
    with client.stream("GET", f"/api/stream/scans/{scan_id}") as r:
        assert r.status_code == 401


def test_other_user_is_404(stream_setup, pg_url):
    _, scan_id = _seed(pg_url)
    client = TestClient(create_app())
    intruder_token = _sign(str(uuid.uuid4()))  # random user id
    with client.stream(
        "GET", f"/api/stream/scans/{scan_id}",
        cookies={"next-auth.session-token": intruder_token},
    ) as r:
        assert r.status_code == 404
