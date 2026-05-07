"""Wrapper that turns SQS params into a ScanConfig and runs the agent.

The agent (osint.run.scan) writes a JSON file to scans_dir; we use a
tempdir, read the resulting file, and return its bytes for the worker
to upload to S3.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import tempfile
from pathlib import Path
from typing import Any

import structlog

from osint.run import scan as run_scan_async
from osint.types import ScanConfig


def _build_config(params: dict[str, Any]) -> ScanConfig:
    """Translate SQS params into a ScanConfig.

    SQS params shape (matches Phase 2 Next.js submit form):
      { subject, agent, preset?, goal?, budget_usd?, max_calls?, max_seconds?, ... }
    Anything not provided falls back to ScanConfig defaults.

    Note: the SQS-side parameter names (`max_calls`, `max_seconds`, `agent`)
    differ from the ScanConfig field names (`max_tool_calls`,
    `max_wall_clock_sec`, `agent_version`). We translate here so the wire
    format stays human-friendly while the model stays canonical.
    """
    kwargs: dict[str, Any] = {}
    if "agent" in params:
        kwargs["agent_version"] = params["agent"]
    if "budget_usd" in params:
        kwargs["budget_usd"] = float(params["budget_usd"])
    if "max_calls" in params:
        kwargs["max_tool_calls"] = int(params["max_calls"])
    if "max_seconds" in params:
        kwargs["max_wall_clock_sec"] = int(params["max_seconds"])
    if "preset" in params:
        kwargs["preset"] = params["preset"]
    if "goal" in params:
        kwargs["goal"] = params["goal"]
    return ScanConfig(**kwargs)


def execute_scan(*, scan_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run the agent end-to-end.

    Returns: { result_bytes: bytes, total_cost_usd: float, total_tool_calls: int }
    Raises whatever the agent raises (worker catches and marks failed).
    """
    subject = params.get("subject")
    if not subject:
        raise ValueError("params.subject is required")

    if os.environ.get("OSINT_E2E_MOCK_SCAN") == "1":
        log = structlog.get_logger("worker.mock_scan").bind(scan_id=scan_id)
        log.info("scan.pass.start")
        time.sleep(1)
        log.info("tool.started", tool_name="web_search", args={"query": subject})
        time.sleep(10)
        log.info(
            "tool.finished",
            tool_name="web_search",
            args={"query": subject},
            result_count=1,
        )
        time.sleep(1)
        log.info("scan.pass.synthesize")
        time.sleep(1)
        result = {
            "scan_id": scan_id,
            "subject": subject,
            "report": {
                "text": f"# E2E Smoke Report\n\nSynthetic result for {subject}.",
            },
            "tool_calls": [
                {
                    "tool_name": "web_search",
                    "args": {"query": subject},
                    "result_count": 1,
                },
            ],
        }
        return {
            "result_bytes": json.dumps(result).encode("utf-8"),
            "total_cost_usd": 0.0,
            "total_tool_calls": 1,
        }

    config = _build_config(params)

    with tempfile.TemporaryDirectory() as tmp:
        scans_dir = Path(tmp)
        result = asyncio.run(run_scan_async(
            subject=subject, config=config, scans_dir=scans_dir,
        ))
        # The agent writes <internal_scan_id>.json into scans_dir. Find it.
        # The agent's internal scan_id is distinct from our SQS scan_id, so
        # we can't reconstruct the path — glob for *.json instead. Should be
        # exactly one (the agent also writes a .md sibling, which we ignore).
        json_files = list(scans_dir.glob("*.json"))
        if not json_files:
            raise RuntimeError("agent produced no scan JSON")
        result_bytes = json_files[0].read_bytes()

    # ScanResult exposes total_cost_usd directly. There's no
    # total_tool_calls field — the tool-call count is len(tool_calls).
    return {
        "result_bytes": result_bytes,
        "total_cost_usd": float(result.total_cost_usd),
        "total_tool_calls": len(result.tool_calls),
    }
