import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from osint.state import ScanState


def new_scan_id() -> str:
    return uuid.uuid4().hex


async def write_scan_json(
    scans_dir: Path,
    state: ScanState,
    status: Literal["done", "failed"],
) -> Path:
    scans_dir.mkdir(parents=True, exist_ok=True)
    path = scans_dir / f"{state.scan_id}.json"
    completed_at = datetime.now(timezone.utc)
    created_at = completed_at - timedelta(seconds=state.wall_clock_elapsed)
    payload = {
        "scan_id": state.scan_id,
        "created_at": created_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "status": status,
        "subject": state.subject,
        "extracted_identifiers": state.extracted_identifiers,
        "config": state.config.model_dump(mode="json"),
        "tool_calls": [tc.model_dump(mode="json") for tc in state.tool_calls],
        "report": state.report,
        "tool_cost_usd": state.tool_cost_usd,
        "llm_cost_usd": state.llm_cost_usd,
        "llm_input_tokens": state.llm_input_tokens,
        "llm_output_tokens": state.llm_output_tokens,
        "total_cost_usd": state.total_cost_usd,
        "duration_sec": state.wall_clock_elapsed,
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path
