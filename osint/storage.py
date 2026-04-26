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
        "pass_reports": state.pass_reports,
        "report": state.report,
        # v2 lead-queue artifacts. Empty for v1. Each Lead/Finding is a
        # Pydantic model in v2; defensively fall through for any already-
        # dict entries (e.g. tests that hand-construct ScanState).
        "findings": [
            f.model_dump(mode="json") if hasattr(f, "model_dump") else f
            for f in state.findings
        ],
        "leads_log": [
            l.model_dump(mode="json") if hasattr(l, "model_dump") else l
            for l in state.leads_log
        ],
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

    # ── Pass evolution (only for multi-pass scans) ─────────────────────────
    # Show prior-pass drafts so a reader can see how the report evolved.
    # The FINAL pass's report is already the body above; we render passes
    # 1..N-1 here as collapsed-style sections. For a single-pass scan this
    # block is omitted entirely.
    if len(state.pass_reports) > 1:
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("## Pass Evolution")
        parts.append("")
        parts.append(
            f"This scan ran **{len(state.pass_reports)} passes**. The main "
            "report above is the final pass; below are the prior drafts so "
            "you can see how the investigation deepened."
        )
        # Skip the LAST entry — that one IS the body above.
        for entry in state.pass_reports[:-1]:
            pn = entry.get("pass_num", "?")
            stop = entry.get("stop_reason")
            cap_note = f" (cap-cut: {stop})" if stop else ""
            parts.append("")
            parts.append(f"### Pass {pn} draft{cap_note}")
            parts.append("")
            r = entry.get("report") or {}
            text = r.get("text") if isinstance(r, dict) else None
            if text:
                parts.append(str(text).rstrip())
            elif r:
                parts.append("```json")
                parts.append(json.dumps(r, indent=2, default=str, ensure_ascii=False))
                parts.append("```")
            else:
                parts.append("_(no report produced for this pass)_")

    # ── v2 lead-queue artifacts ────────────────────────────────────────────
    # Only rendered when the v2 runner populated them; v1 leaves both lists
    # empty so this block is skipped entirely (no empty headings).
    # Lead/Finding objects on state are Pydantic models — we read fields via
    # `model_dump()` so this code is robust to either dict or model entries.
    def _as_dict(x):
        return x.model_dump(mode="json") if hasattr(x, "model_dump") else dict(x)

    if state.leads_log:
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append("## Leads processed")
        parts.append("")
        for i, lead_obj in enumerate(state.leads_log, 1):
            lead = _as_dict(lead_obj)
            parts.append(
                f"{i}. **[{lead.get('kind')}]** "
                f"(priority={lead.get('priority')}, depth={lead.get('depth')}) "
                f"{lead.get('description')}"
            )
        parts.append("")

    if state.findings:
        parts.append("---")
        parts.append("")
        parts.append("## Findings (raw)")
        parts.append("")
        for finding_obj in state.findings:
            f = _as_dict(finding_obj)
            ev = f.get("evidence") or [{}]
            tc_id = ev[0].get("tool_call_id", "?") if isinstance(ev[0], dict) else "?"
            parts.append(
                f"- **({f.get('confidence')})** {f.get('claim')}  ← {tc_id}"
            )
        parts.append("")

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
