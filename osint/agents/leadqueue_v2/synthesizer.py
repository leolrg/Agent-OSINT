"""Synthesizer: one LLM call to merge all findings into the final report."""
from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from osint.agents.leadqueue_v2.prompts import (
    SYNTHESIZER_SYSTEM,
    format_findings_compact,
)
from osint.agents.leadqueue_v2.queue import Finding
from osint.agents.react_v1.prompts import parse_report  # reuse v1's parser


async def synthesize(
    *,
    subject: str,
    findings: list[Finding],
    llm: BaseChatModel,
    cost_cb: Any,
) -> dict:
    """Returns a parsed-report dict matching parse_report()'s schema:
    {"extracted_identifiers": {...}, "report": {"text": "..."}}."""
    findings_block = (
        format_findings_compact(findings, max_chars=20_000)
        if findings else "(no findings)"
    )
    user_msg = (
        f"SUBJECT:\n{subject}\n\nFINDINGS:\n{findings_block}\n\n"
        f"Produce the final report per the system prompt's format."
    )
    msgs = [
        SystemMessage(content=SYNTHESIZER_SYSTEM),
        HumanMessage(content=user_msg),
    ]
    # Only forward cost_cb if it's an actual callback handler. Tests pass
    # MagicMock() which can otherwise interfere with LangChain plumbing;
    # production code passes LLMCostCallback (a BaseCallbackHandler).
    callbacks: list[Any] = (
        [cost_cb] if isinstance(cost_cb, BaseCallbackHandler) else []
    )

    # First attempt
    result = await llm.ainvoke(msgs, config={"callbacks": callbacks})
    text = result.content or ""
    if not text.strip():
        # Grok-4.20 reasoning-mode 0-token bug: retry once.
        result = await llm.ainvoke(msgs, config={"callbacks": callbacks})
        text = result.content or ""
    return parse_report(text)
