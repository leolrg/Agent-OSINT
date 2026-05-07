"""structlog processor that fans agent log events out to Redis.

Used in the worker only. CLI runs (osint.cli) leave the structlog
config alone — see osint/log.py for the default chain.

Design:
- Best-effort. Any Redis error is swallowed so the agent keeps running.
- Maintains a capped history list (default 100 events, 24h TTL) so
  SSE clients that connect mid-scan can replay recent events.
- Returns the event_dict unchanged so other processors (e.g. the
  console renderer) still get to write to stdout.
- Phase 2: tool.started / tool.finished events are enriched with
  `display_label` and `arg_summary` (from osint.worker.tool_labels)
  so the UI never sees internal tool names. Each event also gets a
  monotonic per-scan `seq` integer for client-side de-dup across SSE
  reconnects.
"""
from __future__ import annotations

import json
import time
from typing import Any

import redis as _redis

from osint.worker.tool_labels import describe_tool_call


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
        self._seq = 0

    def __call__(self, logger: Any, method_name: str, event_dict: dict) -> dict:
        seq = self._seq
        self._seq += 1
        out: dict[str, Any] = {
            "ts": time.time(),
            "level": method_name,
            "seq": seq,
            **event_dict,
        }
        # Enrich tool events.
        if out.get("event") in ("tool.started", "tool.finished"):
            tool_name = out.get("tool_name") or out.get("tool")
            if tool_name:
                label, arg = describe_tool_call(tool_name, out.get("args", {}) or {})
                out["display_label"] = label
                out["arg_summary"] = arg
        payload = json.dumps(out, default=str)
        try:
            self.redis.publish(self.channel, payload)
            self.redis.lpush(self.history_key, payload)
            self.redis.ltrim(self.history_key, 0, self.history_cap - 1)
            self.redis.expire(self.history_key, self.HISTORY_TTL_SECONDS)
        except Exception:
            # Never let event sink failure break the agent.
            pass
        return event_dict
