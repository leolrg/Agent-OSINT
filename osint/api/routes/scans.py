"""Auth-gated scan detail and tool-step endpoints."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Optional

import redis as _redis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from osint.api.aws import s3_client
from osint.api.dependencies import User, current_user
from osint.db.models import Scan


router = APIRouter()


def _redis_client() -> _redis.Redis:
    """Override target for tests (fakeredis injection)."""
    return _redis.from_url(os.environ["REDIS_URL"])


def _presign(s3_key: str) -> Optional[str]:
    if not s3_key:
        return None
    try:
        return s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": os.environ["S3_BUCKET"], "Key": s3_key},
            ExpiresIn=3600,
        )
    except Exception:
        return None


@router.get("/api/scans/{scan_id}")
async def get_scan(scan_id: uuid.UUID, user: User = Depends(current_user)) -> dict:
    # Imported lazily so that the engine is constructed only after the test
    # fixture has set DATABASE_URL via monkeypatch.
    from osint.db.session import db_session

    with db_session() as s:
        sc = s.execute(select(Scan).where(Scan.id == scan_id)).scalar_one_or_none()
        # Same response shape for "not yours" and "doesn't exist".
        if sc is None or str(sc.user_id) != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return {
            "id": str(sc.id),
            "status": sc.status,
            "agent": sc.agent,
            "params": sc.params,
            "error_message": sc.error_message,
            "total_cost_usd": float(sc.total_cost_usd) if sc.total_cost_usd else None,
            "total_tool_calls": sc.total_tool_calls,
            "created_at": sc.created_at.isoformat() if sc.created_at else None,
            "started_at": sc.started_at.isoformat() if sc.started_at else None,
            "completed_at": sc.completed_at.isoformat() if sc.completed_at else None,
            "s3_url": _presign(sc.s3_key) if sc.s3_key else None,
        }


def build_steps_from_events(
    events: list[dict],
    *,
    started_at: datetime | None = None,
) -> list[dict]:
    """Convert Redis scan events into the compact step shape used by the UI."""
    base_ts = started_at.timestamp() if started_at else None
    if base_ts is None:
        for event in events:
            if isinstance(event.get("ts"), (int, float)):
                base_ts = float(event["ts"])
                break
    if base_ts is None:
        base_ts = 0.0

    def key_for(event: dict) -> tuple[str, str]:
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        try:
            args_key = json.dumps(args, sort_keys=True, default=str)
        except TypeError:
            args_key = str(args)
        return str(event.get("tool_name") or event.get("tool") or ""), args_key

    def step_for(event: dict, *, pending: bool = False) -> dict:
        ts_raw = event.get("ts")
        ts = float(ts_raw) if isinstance(ts_raw, (int, float)) else base_ts
        label = event.get("display_label") or event.get("tool_name") or event.get("tool") or "Tool"
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        preview: list[str] = []
        if event.get("error"):
            preview.append(str(event["error"]))
        if event.get("result_count") is not None:
            preview.append(f"{event['result_count']} results")
        if event.get("result_size_bytes") is not None:
            preview.append(f"{event['result_size_bytes']} bytes")
        if pending:
            preview.append("Still running")
        return {
            "ts": max(0, int(ts - base_ts)),
            "displayLabel": str(label),
            "argSummary": str(event.get("arg_summary") or ""),
            "fullArgs": args,
            "responsePreview": "\n".join(preview),
        }

    steps: list[tuple[float, dict]] = []
    pending: dict[tuple[str, str], dict] = {}
    for event in events:
        name = event.get("event")
        if name not in {"tool.started", "tool.finished"}:
            continue
        key = key_for(event)
        if name == "tool.started":
            pending[key] = event
            continue
        pending.pop(key, None)
        ts = float(event["ts"]) if isinstance(event.get("ts"), (int, float)) else base_ts
        steps.append((ts, step_for(event)))

    for event in pending.values():
        ts = float(event["ts"]) if isinstance(event.get("ts"), (int, float)) else base_ts
        steps.append((ts, step_for(event, pending=True)))

    steps.sort(key=lambda item: item[0])
    return [step for _, step in steps]


@router.get("/api/scans/{scan_id}/steps")
async def get_scan_steps(scan_id: uuid.UUID, user: User = Depends(current_user)) -> dict:
    from osint.db.session import db_session

    with db_session() as s:
        sc = s.execute(select(Scan).where(Scan.id == scan_id)).scalar_one_or_none()
        if sc is None or str(sc.user_id) != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        started_at = sc.started_at

    history_key = f"scan:{scan_id}:events"
    raw_events = _redis_client().lrange(history_key, 0, -1)
    events: list[dict] = []
    for raw in reversed(raw_events):
        data = raw.decode() if isinstance(raw, bytes) else raw
        try:
            event = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            events.append(event)

    return {"steps": build_steps_from_events(events, started_at=started_at)}
