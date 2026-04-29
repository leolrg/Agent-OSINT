"""RedisEventSink enriches tool events with display_label, arg_summary, seq."""
from __future__ import annotations

import json

import fakeredis

from osint.worker.event_sink import RedisEventSink


def _published(redis_client, channel="scan:abc"):
    """Drain pubsub messages for a channel."""
    pubsub = redis_client.pubsub()
    pubsub.subscribe(channel)
    next(pubsub.listen())  # subscribe-ack
    msgs = []
    while True:
        try:
            m = pubsub.get_message(timeout=0.05)
        except Exception:
            break
        if not m:
            break
        if m["type"] == "message":
            msgs.append(json.loads(m["data"]))
    return msgs


def test_tool_started_event_gets_display_label_and_arg_summary():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(
        logger=None, method_name="info",
        event_dict={
            "event": "tool.started",
            "tool_name": "web_search",
            "args": {"query": "Jane Doe ML"},
        },
    )
    history = redis.lrange("scan:abc:events", 0, 0)
    payload = json.loads(history[0])
    assert payload["event"] == "tool.started"
    assert payload["display_label"] == "Web search"
    assert payload["arg_summary"] == '"Jane Doe ML"'


def test_tool_finished_event_also_enriched():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(
        logger=None, method_name="info",
        event_dict={
            "event": "tool.finished",
            "tool_name": "apify_linkedin",
            "args": {"profile_url": "https://www.linkedin.com/in/jane-doe-89a/"},
        },
    )
    history = redis.lrange("scan:abc:events", 0, 0)
    payload = json.loads(history[0])
    assert payload["display_label"] == "LinkedIn"
    assert payload["arg_summary"] == "jane-doe-89a"


def test_non_tool_event_not_enriched():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(
        logger=None, method_name="info",
        event_dict={"event": "scan.started"},
    )
    history = redis.lrange("scan:abc:events", 0, 0)
    payload = json.loads(history[0])
    assert "display_label" not in payload
    assert "arg_summary" not in payload


def test_seq_is_monotonic_per_sink():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    for i in range(3):
        sink(logger=None, method_name="info", event_dict={"event": f"e{i}"})
    history = redis.lrange("scan:abc:events", 0, -1)
    seqs = [json.loads(h)["seq"] for h in history]
    # LPUSH stores newest first, so seq sequence is 2,1,0 reading left-to-right
    assert seqs == [2, 1, 0]


def test_unknown_tool_falls_through_to_generic_label():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(
        logger=None, method_name="info",
        event_dict={
            "event": "tool.started",
            "tool_name": "internal_secret_tool",
            "args": {"foo": "bar"},
        },
    )
    history = redis.lrange("scan:abc:events", 0, 0)
    payload = json.loads(history[0])
    assert payload["display_label"] == "Tool"
    assert payload["arg_summary"] == ""
