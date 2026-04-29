"""GET /api/scans/{scan_id} — auth-gated scan detail with presigned S3 URL."""
from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from osint.api.aws import s3_client
from osint.api.dependencies import User, current_user
from osint.db.models import Scan


router = APIRouter()


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
