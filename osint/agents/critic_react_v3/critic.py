"""Critic call + verdict parser for critic_react_v3.

The critic is one LLM invocation, no tools. It reads the goal, the
agent's draft report, and a tool-call summary, and returns either
ACCEPT or REJECT with a list of gaps. Parser failures default to
ACCEPT to avoid infinite loops on parser fragility (per spec).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Verdict:
    accept: bool
    gaps: list[str] = field(default_factory=list)


_VERDICT_RE = re.compile(r"VERDICT\s*:\s*(ACCEPT|REJECT)", re.IGNORECASE)


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
    # REJECT — collect bullets after a "GAPS:" header (case-insensitive).
    lines = text.splitlines()
    gaps: list[str] = []
    in_gaps = False
    for line in lines:
        if re.match(r"^\s*GAPS\s*:", line, re.IGNORECASE):
            in_gaps = True
            continue
        if in_gaps:
            stripped = line.strip()
            if stripped.startswith("- "):
                gaps.append(stripped[2:].strip())
    return Verdict(accept=False, gaps=gaps)
