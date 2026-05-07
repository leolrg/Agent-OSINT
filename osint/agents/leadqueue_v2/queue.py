"""Priority queue of leads + the per-lead Lead/Source/Finding models.

The queue is in-memory only — its history is captured in
ScanState.leads_log as leads are popped, so an audit trail of the
investigation survives the scan even though the live queue does not.

Dedup happens by description hash (lower-cased, whitespace-stripped) so
the LLM can't spam the queue with trivial restatements of the same lead.
The seen-set is *append-only* — popping a lead does not re-open its slot.
That is intentional: if the verifier proposes a lead that was already
processed, it should be rejected, not re-run.
"""
from __future__ import annotations

import heapq
import itertools
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Source(BaseModel):
    tool_call_id: str
    snippet_quote: str   # the literal text from the tool result


class Finding(BaseModel):
    id: str
    claim: str
    evidence: list[Source] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
    lead_id: str
    tags: list[str] = Field(default_factory=list)


class Lead(BaseModel):
    id: str
    kind: str               # informal tag; logging + dedup only, NOT branching
    description: str
    priority: int           # higher = process first
    depth: int = 0          # 0 = root; deeper = generated from a prior lead
    parent_lead_id: str | None = None
    created_at: datetime

    def dedup_key(self) -> str:
        """Normalised hash key for dedup. Lower-cased, whitespace-collapsed."""
        return " ".join(self.description.lower().split())


class LeadQueue:
    """Priority queue with append-only seen-set for dedup.

    Internals: a binary heap of (-priority, insertion_counter, Lead). The
    counter breaks ties as FIFO and keeps the heap-ordered without
    needing Lead to be comparable.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, Lead]] = []
        self._counter = itertools.count()
        self._seen: set[str] = set()

    def push(self, lead: Lead) -> bool:
        """Push a lead. Returns False if dedup'd (lead's key has been seen)."""
        key = lead.dedup_key()
        if key in self._seen:
            return False
        self._seen.add(key)
        heapq.heappush(self._heap, (-lead.priority, next(self._counter), lead))
        return True

    def pop(self) -> Lead | None:
        if not self._heap:
            return None
        return heapq.heappop(self._heap)[2]

    def is_seen(self, lead: Lead) -> bool:
        return lead.dedup_key() in self._seen

    def empty(self) -> bool:
        return not self._heap

    def __len__(self) -> int:
        return len(self._heap)
