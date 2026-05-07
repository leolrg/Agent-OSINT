"""Unit tests for RedisEventSink — using fakeredis, no real Redis."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import fakeredis
import pytest

from osint.worker.event_sink import RedisEventSink


def test_publishes_to_scan_channel():
    redis = fakeredis.FakeRedis()
    pubsub = redis.pubsub()
    pubsub.subscribe("scan:abc")
    next(pubsub.listen())  # consume subscribe-ack

    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(logger=None, method_name="info",
         event_dict={"event": "tool.started", "tool": "google_search"})

    msg = next(pubsub.listen())
    assert msg["type"] == "message"
    payload = json.loads(msg["data"])
    assert payload["event"] == "tool.started"
    assert payload["tool"] == "google_search"
    assert payload["level"] == "info"
    assert "ts" in payload


def test_appends_to_history_list_with_cap():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis, history_cap=5)
    for i in range(10):
        sink(logger=None, method_name="info", event_dict={"event": f"e{i}"})
    history = redis.lrange("scan:abc:events", 0, -1)
    assert len(history) == 5
    # LPUSH stores newest-first; last 5 events should be e5..e9.
    events = [json.loads(h) for h in history]
    assert [e["event"] for e in events] == ["e9", "e8", "e7", "e6", "e5"]


def test_swallows_redis_errors():
    redis = MagicMock()
    redis.publish.side_effect = ConnectionError("boom")
    redis.lpush.side_effect = ConnectionError("boom")
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    # Should not raise — agent must not break when Redis is down.
    out = sink(logger=None, method_name="info", event_dict={"event": "x"})
    assert out == {"event": "x"}  # passes through to next processor


def test_passes_through_event_dict_unchanged():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    in_dict = {"event": "x", "n": 1}
    out = sink(logger=None, method_name="info", event_dict=in_dict)
    assert out is in_dict  # structlog requires the same dict back
