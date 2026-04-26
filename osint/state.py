import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from osint.types import ScanConfig, ToolCallRecord


class StopReason(str, Enum):
    NONE = "none"
    BUDGET = "budget"
    MAX_CALLS = "max_calls"
    WALL_CLOCK = "wall_clock"
    FINAL_REPORT = "final_report"


@dataclass
class ScanState:
    scan_id: str
    subject: str
    config: ScanConfig
    started_at: float = field(default_factory=time.monotonic)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    extracted_identifiers: dict[str, Any] = field(default_factory=dict)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    # Full LangGraph message history captured after the agent loop ends.
    # Each entry is a serialized BaseMessage dict (see _serialize_messages
    # in osint/run.py): SystemMessage, HumanMessage, AIMessage (with
    # tool_calls), and ToolMessage. Includes the synthesis exchange too
    # when a cap-cut triggered the synthesis fallback.
    messages: list[dict[str, Any]] = field(default_factory=list)
    _has_report: bool = False

    @property
    def tool_cost_usd(self) -> float:
        return sum(tc.cost_usd for tc in self.tool_calls)

    @property
    def llm_cost_usd(self) -> float:
        p = self.config.llm.pricing
        return (
            self.llm_input_tokens * p.input_per_mtok_usd / 1_000_000
            + self.llm_output_tokens * p.output_per_mtok_usd / 1_000_000
        )

    @property
    def total_cost_usd(self) -> float:
        return self.tool_cost_usd + self.llm_cost_usd

    @property
    def wall_clock_elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def should_stop(self) -> tuple[bool, StopReason]:
        if self.total_cost_usd >= self.config.budget_usd:
            return True, StopReason.BUDGET
        if len(self.tool_calls) >= self.config.max_tool_calls:
            return True, StopReason.MAX_CALLS
        if self.wall_clock_elapsed >= self.config.max_wall_clock_sec:
            return True, StopReason.WALL_CLOCK
        return False, StopReason.NONE

    def record_tool_call(self, tc: ToolCallRecord) -> None:
        self.tool_calls.append(tc)

    def record_llm_usage(self, *, input_tokens: int, output_tokens: int) -> None:
        self.llm_input_tokens += max(0, int(input_tokens or 0))
        self.llm_output_tokens += max(0, int(output_tokens or 0))

    def record_final_report(self, report: dict[str, Any], identifiers: dict[str, Any] | None = None) -> None:
        self.report = report
        if identifiers is not None:
            self.extracted_identifiers = identifiers
        self._has_report = True

    def has_final_report(self) -> bool:
        return self._has_report
