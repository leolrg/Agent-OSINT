"""structlog processor that fans agent log events out to Redis.

Used in the worker only. CLI runs (osint.cli) leave the structlog
config alone — see osint/log.py for the default chain.

Design:
- Best-effort. Any Redis error is swallowed so the agent keeps running.
- Maintains a capped history list (default 100 events, 24h TTL) so
  SSE clients that connect mid-scan can replay recent events.
- Returns the event_dict unchanged so other processors (e.g. the
  console renderer) still get to write to stdout.
"""
from __future__ import annotations

import json
import time
from typing import Any

import redis as _redis


class RedisEventSink:
    HISTORY_CAP_DEFAULT = 100
    HISTORY_TTL_SECONDS = 86400  # 24h

    def __init__(
        self,
        scan_id: str,
        redis_client: _redis.Redis,
        history_cap: int = HISTORY_CAP_DEFAULT,
    ) -> None:
        self.scan_id = scan_id
        self.redis = redis_client
        self.history_cap = history_cap
        self.channel = f"scan:{scan_id}"
        self.history_key = f"scan:{scan_id}:events"

    def __call__(self, logger: Any, method_name: str, event_dict: dict) -> dict:
        payload = json.dumps({
            "ts": time.time(),
            "level": method_name,
            **event_dict,
        }, default=str)
        try:
            self.redis.publish(self.channel, payload)
            self.redis.lpush(self.history_key, payload)
            self.redis.ltrim(self.history_key, 0, self.history_cap - 1)
            self.redis.expire(self.history_key, self.HISTORY_TTL_SECONDS)
        except Exception:
            # Never let event sink failure break the agent.
            pass
        return event_dict
