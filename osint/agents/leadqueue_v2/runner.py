"""LeadQueueV2Runner — entrypoint for the lead-queue agent.

Phases (per the spec):
  1. Seed: push identity-lock lead onto queue
  2. Main loop: pop -> process -> record -> push new leads, until empty or stop
  3. Synthesize: findings -> draft report
  4. Verifier loop: <= max_verifier_iterations
       - if satisfied: break
       - else: push verifier's new_leads; drain main loop; re-synth
  5. Return parsed report
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel

from osint.agents.leadqueue_v2.processor import process_one_lead
from osint.agents.leadqueue_v2.queue import Lead, LeadQueue
from osint.agents.leadqueue_v2.synthesizer import synthesize
from osint.agents.leadqueue_v2.verifier import verify
from osint.log import logger
from osint.state import ScanState, StopReason


def _identity_lock_lead(subject: str) -> Lead:
    return Lead(
        id=f"l-{uuid.uuid4().hex[:8]}",
        kind="identity_lock",
        description=(
            f"Verify the identity of '{subject}'. Find >=3 cross-reference "
            f"points (school + year, city, employer, distinct identifier) "
            f"that all match. Output identity-lock fact + initial leads "
            f"(handles to probe, URLs to extract, organizations to investigate)."
        ),
        priority=100,
        depth=0,
        parent_lead_id=None,
        created_at=datetime.now(timezone.utc),
    )


async def _drain_queue(
    *,
    queue: LeadQueue,
    subject: str,
    state: ScanState,
    llm: BaseChatModel,
    tools: list[Any],
    cost_cb: Any,
) -> None:
    """Pop and process leads until queue is empty or scan should stop.

    Mutates state in place: extends state.findings, appends to
    state.leads_log on each completed lead. New leads from the processor
    go back onto `queue` (subject to dedup)."""
    while not queue.empty():
        should_stop, _reason = state.should_stop()
        if should_stop:
            break
        lead = queue.pop()
        if lead is None:
            break
        findings, new_leads = await process_one_lead(
            subject=subject,
            lead=lead,
            all_findings=state.findings,
            llm=llm,
            tools=tools,
            cost_cb=cost_cb,
        )
        state.findings.extend(findings)
        state.leads_log.append(lead)
        for nl in new_leads:
            queue.push(nl)


class LeadQueueV2Runner:
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
        queue = LeadQueue()

        # Phase 1: seed
        queue.push(_identity_lock_lead(subject))

        # Phase 2: main loop
        await _drain_queue(
            queue=queue, subject=subject, state=state,
            llm=llm, tools=tools, cost_cb=cost_cb,
        )

        # Stop reason check before we spend on synth+verify
        should_stop, stop_reason = state.should_stop()

        # Phase 3: synthesize
        parsed = await synthesize(
            subject=subject, findings=state.findings,
            llm=llm, cost_cb=cost_cb,
        )

        # Phase 4: verifier loop (skipped if cap-cut already)
        if not should_stop:
            while state.verifier_iterations < config.max_verifier_iterations:
                vresult = await verify(
                    subject=subject,
                    report_text=parsed.get("report", {}).get("text") or "",
                    findings=state.findings,
                    leads_log=state.leads_log,
                    llm=llm, cost_cb=cost_cb,
                )
                if vresult.satisfied:
                    break
                # Push new leads, drain, re-synthesize.
                for nl in vresult.new_leads:
                    queue.push(nl)
                await _drain_queue(
                    queue=queue, subject=subject, state=state,
                    llm=llm, tools=tools, cost_cb=cost_cb,
                )
                parsed = await synthesize(
                    subject=subject, findings=state.findings,
                    llm=llm, cost_cb=cost_cb,
                )
                state.verifier_iterations += 1
                # Re-check stop conditions; verifier loop respects budget too.
                should_stop, stop_reason = state.should_stop()
                if should_stop:
                    break

        return parsed, stop_reason if should_stop else None
