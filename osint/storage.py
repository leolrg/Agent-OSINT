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
        "messages": state.messages,
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


async def write_scan_markdown(
    scans_dir: Path,
    state: ScanState,
    status: Literal["done", "failed"],
) -> Path:
    """Write a human-readable Markdown view of the scan alongside the JSON.

    The JSON file is the source of truth for tooling; this `.md` is a
    convenient render for humans (open in the IDE, share, paste into a doc).
    Layout: header with metadata → the prose report from `state.report["text"]`
    (or a JSON dump if the report doesn't have prose) → extracted identifiers
    code block → tool-call audit log.
    """
    scans_dir.mkdir(parents=True, exist_ok=True)
    path = scans_dir / f"{state.scan_id}.md"
    completed_at = datetime.now(timezone.utc)
    created_at = completed_at - timedelta(seconds=state.wall_clock_elapsed)

    parts: list[str] = []

    # ── Header / metadata ──────────────────────────────────────────────────
    parts.append(f"# Scan `{state.scan_id}`\n")
    parts.append(f"**Subject:** {state.subject}\n")
    parts.append("")
    parts.append(f"- **Status:** {status}")
    parts.append(f"- **Created:** {created_at.isoformat()}")
    parts.append(f"- **Completed:** {completed_at.isoformat()}")
    parts.append(f"- **Duration:** {state.wall_clock_elapsed:.1f}s")
    parts.append(f"- **Tool calls:** {len(state.tool_calls)}")
    parts.append(
        f"- **Cost:** ${state.total_cost_usd:.4f} "
        f"(tool ${state.tool_cost_usd:.4f} + LLM ${state.llm_cost_usd:.4f})"
    )
    parts.append(
        f"- **LLM tokens:** {state.llm_input_tokens:,} in / "
        f"{state.llm_output_tokens:,} out"
    )
    parts.append("")
    parts.append("---")
    parts.append("")

    # ── Body: the prose report ─────────────────────────────────────────────
    if isinstance(state.report, dict) and state.report.get("text"):
        parts.append(str(state.report["text"]).rstrip())
    elif state.report:
        # Old structured-envelope report — render as a JSON code block.
        parts.append("```json")
        parts.append(json.dumps(state.report, indent=2, default=str, ensure_ascii=False))
        parts.append("```")
    else:
        parts.append("_(no report was produced)_")

    # ── Extracted identifiers ──────────────────────────────────────────────
    if state.extracted_identifiers:
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("## Extracted Identifiers")
        parts.append("")
        parts.append("```json")
        parts.append(
            json.dumps(state.extracted_identifiers, indent=2, default=str, ensure_ascii=False)
        )
        parts.append("```")

    # ── Message-history summary ────────────────────────────────────────────
    if state.messages:
        from collections import Counter
        type_counts = Counter(m.get("type", "?") for m in state.messages)
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("## Message Log Summary")
        parts.append("")
        parts.append(
            f"Captured **{len(state.messages)} messages** during the scan: "
            + ", ".join(f"{n} {t}" for t, n in sorted(type_counts.items()))
            + ". Full per-message contents (system prompt, every AI turn with "
            "reasoning + tool_calls, each ToolMessage payload) are in the "
            "JSON file's `messages` field."
        )

    # ── Tool-call log ──────────────────────────────────────────────────────
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("## Tool Call Log")
    parts.append("")
    if state.tool_calls:
        for tc in state.tool_calls:
            args = json.dumps(tc.input, default=str, separators=(",", ":"), ensure_ascii=False)
            if len(args) > 140:
                args = args[:140] + "…"
            err = f" — **error:** `{tc.error}`" if tc.error else ""
            parts.append(
                f"{tc.turn}. **{tc.tool}** — `{args}` (${tc.cost_usd:.4f}){err}"
            )
    else:
        parts.append("_(no tool calls were made)_")
    parts.append("")

    path.write_text("\n".join(parts), encoding="utf-8")
    return path
