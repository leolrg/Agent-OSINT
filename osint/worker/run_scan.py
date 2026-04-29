"""Wrapper that turns SQS params into a ScanConfig and runs the agent.

Phase 1 Task 9: stub returns canned bytes (test patches it).
Phase 1 Task 10: real implementation calling osint.run.scan(...).
"""
from __future__ import annotations

from typing import Any


def execute_scan(*, scan_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run the agent. Returns dict with `result_bytes`, `total_cost_usd`,
    `total_tool_calls`. Raises on failure.
    """
    raise NotImplementedError("Real implementation lands in Task 10")
