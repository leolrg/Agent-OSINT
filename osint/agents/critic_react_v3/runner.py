"""CriticReactV3Runner — ReAct agent + open-question ledger + adversarial critic.

Outer loop:
  1. Build system prompt from subject + preset + goal + tools.
  2. Run a fresh create_react_agent to terminal AIMessage (one engagement).
  3. Parse the open-question ledger from the terminal AIMessage.
     - If `open` non-empty: append synthetic "you have open questions" user
       message and re-run from step 2.
  4. Otherwise call the critic. ACCEPT -> done. REJECT -> append "reviewer
     flagged these gaps" message and re-run from step 2.
  5. Cap critic rejections at config.max_critic_rejections.

Hard caps (budget, max_calls, wall_clock) preempt the loop. On preemption
fall through to v1's _synthesize so the user always gets *some* report.
"""
from __future__ import annotations

import asyncio
import json as _json
import re
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from osint.agents.critic_react_v3.critic import critic
from osint.agents.critic_react_v3.prompts import build_system_prompt, parse_ledger
from osint.agents.react_v1.prompts import parse_report
from osint.agents.react_v1.runner import _serialize_messages, _synthesize
from osint.errors import ScanStopped
from osint.state import ScanState, StopReason


def _extract_last_ai_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return m.content or ""
    return ""


# v3 drafts contain TWO fenced JSON blocks:
#   1. the open-question ledger (has key "open"), which the system prompt
#      asks the model to put first
#   2. the final identifiers envelope (has key "extracted_identifiers")
# v1's parse_report uses re.search and matches the FIRST fenced block, so we
# must strip the ledger before delegating. We do NOT anchor to start-of-string:
# on critic-rejected re-engagements, the model occasionally prepends a header
# line ("Final answer:" etc.) before the ledger. Instead we walk every fenced
# block in the draft, parse it, and strip the first one whose parsed object
# has an "open" key. That fingerprints the ledger regardless of position.
_FENCED_JSON_ANY = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _strip_leading_ledger(text: str) -> str:
    """Strip the first fenced ```json``` block whose parsed JSON has an `open` key.

    The "open" key is the ledger's signature; the trailing identifiers block
    uses "extracted_identifiers" as its key, so it is unaffected. If no
    ledger-shaped block is present (e.g. cap-cut synthesis output), returns
    the input unchanged.
    """
    if not text:
        return text
    for m in _FENCED_JSON_ANY.finditer(text):
        try:
            data = _json.loads(m.group(1))
        except _json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "open" in data:
            return text[: m.start()] + text[m.end():]
    return text


class CriticReactV3Runner:
    """v3 agent — ReAct + ledger + critic. Implements osint.agents.base.AgentRunner."""

    async def run(
        self,
        *,
        subject: str,
        state: ScanState,
        llm: BaseChatModel,
        tools: list[Any],
        cost_cb: Any,
    ) -> tuple[dict, StopReason | None]:
        config = state.config
        system_text = build_system_prompt(
            subject=subject,
            goal=config.goal,
            preset=config.preset,
            tool_names=sorted(config.enabled_tools),
        )
        messages: list[BaseMessage] = [
            SystemMessage(content=system_text),
            HumanMessage(content="Begin."),
        ]
        invoke_callbacks = [cost_cb] if isinstance(cost_cb, BaseCallbackHandler) else []
        rejections = 0
        last_text = ""
        last_stop_reason: StopReason | None = None

        while True:
            stopped, reason = state.should_stop()
            if stopped:
                last_stop_reason = reason
                break
            if rejections > config.max_critic_rejections:
                last_stop_reason = StopReason.CRITIC_EXHAUSTED
                break

            agent = create_react_agent(llm, tools, prompt=None)
            try:
                agent_result = await asyncio.wait_for(
                    agent.ainvoke(
                        {"messages": messages},
                        config={
                            "recursion_limit": config.max_recursion_per_engagement,
                            "callbacks": invoke_callbacks,
                        },
                    ),
                    timeout=config.max_wall_clock_sec,
                )
            except ScanStopped as e:
                last_stop_reason = StopReason(e.reason)
                break
            except asyncio.TimeoutError:
                last_stop_reason = StopReason.WALL_CLOCK
                break
            except GraphRecursionError:
                last_stop_reason = StopReason.MAX_CALLS
                break

            messages = list(agent_result.get("messages", []))
            last_text = _extract_last_ai_text(messages)
            if not last_text.strip():
                last_stop_reason = StopReason.EMPTY_FINAL
                break

            ledger = parse_ledger(last_text)
            if ledger.open:
                messages.append(HumanMessage(content=(
                    f"You stopped with open questions: {ledger.open}. "
                    f"Continue investigating; you may use any tools. "
                    f"Update your open-question ledger before any final report."
                )))
                continue

            verdict = await critic(
                subject=subject,
                goal=config.goal,
                preset=config.preset,
                draft=last_text,
                tool_calls=state.tool_calls,
                llm=llm,
                cost_cb=cost_cb,
            )
            if verdict.accept:
                last_stop_reason = StopReason.CRITIC_ACCEPTED
                break
            rejections += 1
            messages.append(HumanMessage(content=(
                "A reviewer flagged these gaps:\n- " + "\n- ".join(verdict.gaps) +
                "\n\nAddress each. Use any tools. Update your open-question ledger; "
                "remember the final-report JSON envelope shape."
            )))

        # Cap-cut path on preemption / empty final.
        cap_cut_reasons = {
            StopReason.BUDGET, StopReason.MAX_CALLS,
            StopReason.WALL_CLOCK, StopReason.EMPTY_FINAL,
        }
        state.messages.extend(_serialize_messages(messages))
        if last_stop_reason in cap_cut_reasons:
            synth_text, synth_msgs = await _synthesize(
                llm, subject, state, last_stop_reason.value, cost_cb,
            )
            state.messages.extend(_serialize_messages(synth_msgs))
            parsed = parse_report(synth_text)
            state.record_final_report(
                parsed.get("report") or {},
                identifiers=parsed.get("extracted_identifiers") or {},
            )
            return parsed, last_stop_reason

        # Critic-accepted or critic-exhausted: parse the last engagement's draft.
        # Strip the leading ledger block so parse_report sees the trailing
        # extracted_identifiers block as the report's tail-JSON.
        parsed = parse_report(_strip_leading_ledger(last_text))
        state.record_final_report(
            parsed.get("report") or {},
            identifiers=parsed.get("extracted_identifiers") or {},
        )
        # CRITIC_ACCEPTED is a clean finish — return None for stop_reason.
        return parsed, None if last_stop_reason == StopReason.CRITIC_ACCEPTED else last_stop_reason
