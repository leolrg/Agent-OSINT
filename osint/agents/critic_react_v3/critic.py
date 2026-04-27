"""Critic call + verdict parser for critic_react_v3.

The critic is one LLM invocation, no tools. It reads the goal, the
agent's draft report, and a tool-call summary, and returns either
ACCEPT or REJECT with a list of gaps. Parser failures default to
ACCEPT to avoid infinite loops on parser fragility (per spec).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from osint.agents.critic_react_v3.prompts import PRESET_HINTS


@dataclass
class Verdict:
    accept: bool
    gaps: list[str] = field(default_factory=list)


_VERDICT_RE = re.compile(r"VERDICT\s*:\s*(ACCEPT|REJECT)", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+\S)\s*$")
_SECTION_HEADER_RE = re.compile(r"^\s*[A-Z][A-Z _-]{1,30}\s*:\s*$")
_GAPS_HEADER_RE = re.compile(r"^\s*GAPS\s*:", re.IGNORECASE)


def parse_critic_verdict(text: str) -> Verdict:
    """Parse the critic's free-form output into a Verdict.

    Format expected:
        VERDICT: ACCEPT | REJECT
        GAPS:
        - bullet 1
        - bullet 2

    Missing/malformed VERDICT line → treat as ACCEPT (avoid infinite loops).
    """
    if not text:
        return Verdict(accept=True)
    m = _VERDICT_RE.search(text)
    if not m:
        return Verdict(accept=True)
    decision = m.group(1).upper()
    if decision == "ACCEPT":
        return Verdict(accept=True)
    # REJECT — collect bullets after a "GAPS:" header.
    lines = text.splitlines()
    gaps: list[str] = []
    in_gaps = False
    for line in lines:
        if _GAPS_HEADER_RE.match(line):
            in_gaps = True
            continue
        if not in_gaps:
            continue
        # Stop at any subsequent ALL-CAPS section header (e.g. NOTES:, SUMMARY:).
        if _SECTION_HEADER_RE.match(line):
            in_gaps = False
            continue
        bm = _BULLET_RE.match(line)
        if bm:
            gaps.append(bm.group(1))
    return Verdict(accept=False, gaps=gaps)


_CRITIC_SYSTEM = """\
You are reviewing whether an investigation has met its goal.

Decide: accept the draft, or reject with specific gaps the investigator
should address. A gap is something the goal needs that the draft does
not currently support, OR a concrete identifier in the draft (email,
handle, url, id) that was never followed up on.

Respond in this exact form:

VERDICT: ACCEPT
or
VERDICT: REJECT
GAPS:
- (one bullet per gap)
"""


def _summarize_tool_calls(tool_calls: list[Any]) -> str:
    """One-line histogram of tool-call counts by tool name.

    Operates on project ``ToolCallRecord`` instances (Pydantic model with a
    ``.tool: str`` field, defined in ``osint/types.py``) — NOT LangChain
    ``ToolCall`` TypedDicts (which use ``"name"``). The runner passes
    ``state.tool_calls`` directly, which is ``list[ToolCallRecord]``.

    A dict-style fallback (``tc.get("tool")``) is kept for defensive
    handling of test fixtures and any future call-site that builds
    plain dicts; falls through to ``"unknown"`` for any other shape.
    Returns ``"(no tool calls were made)"`` for an empty list.
    """
    if not tool_calls:
        return "(no tool calls were made)"
    counts: dict[str, int] = {}
    for tc in tool_calls:
        name = getattr(tc, "tool", None) or (tc.get("tool") if isinstance(tc, dict) else None) or "unknown"
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{n}={c}" for n, c in sorted(counts.items()))


async def critic(
    *,
    subject: str,
    goal: str,
    preset: str,
    draft: str,
    tool_calls: list[Any],
    llm: BaseChatModel,
    cost_cb: Any,
) -> Verdict:
    """Single LLM call, no tools. Returns Verdict.

    Parser failures default to ACCEPT (parse_critic_verdict policy).
    Network/API errors propagate — caller decides whether to retry.
    """
    user_msg = (
        f"GOAL: {goal or '(none — use preset hint)'}\n"
        f"PRESET HINT: {PRESET_HINTS.get(preset, PRESET_HINTS['general'])}\n"
        f"SUBJECT: {subject}\n\n"
        f"TOOLS USED (count by name): {_summarize_tool_calls(tool_calls)}\n\n"
        f"DRAFT REPORT:\n{draft}"
    )
    callbacks = [cost_cb] if isinstance(cost_cb, BaseCallbackHandler) else []
    resp = await llm.ainvoke(
        [SystemMessage(content=_CRITIC_SYSTEM), HumanMessage(content=user_msg)],
        config={"callbacks": callbacks},
    )
    return parse_critic_verdict(getattr(resp, "content", "") or "")
