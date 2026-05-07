from datetime import datetime, timezone

import pytest

from osint.agents.leadqueue_v2.queue import (
    Finding,
    Lead,
    LeadQueue,
    Source,
)


def _lead(description: str, priority: int = 50, depth: int = 0) -> Lead:
    return Lead(
        id=f"l-{description[:8]}",
        kind="test",
        description=description,
        priority=priority,
        depth=depth,
        parent_lead_id=None,
        created_at=datetime.now(timezone.utc),
    )


def test_lead_dedup_hash_normalizes_whitespace_and_case():
    """Two leads with descriptions that differ only by whitespace/case
    must hash to the same dedup key — otherwise the LLM can spam the
    queue with trivial variations."""
    a = _lead("Investigate handle simonwen.eth")
    b = _lead("  investigate handle SimonWen.eth  ")
    assert a.dedup_key() == b.dedup_key()


def test_leadqueue_pop_returns_highest_priority_first():
    """Higher priority pops before lower priority. Tie-break by
    insertion order (FIFO within the same priority)."""
    q = LeadQueue()
    q.push(_lead("a", priority=10))
    q.push(_lead("b", priority=100))
    q.push(_lead("c", priority=50))
    assert q.pop().description == "b"
    assert q.pop().description == "c"
    assert q.pop().description == "a"
    assert q.pop() is None


def test_leadqueue_dedup_on_push():
    """Pushing a lead whose description already exists (popped or not)
    is a silent no-op — push() returns False; the queue size doesn't grow."""
    q = LeadQueue()
    assert q.push(_lead("investigate X")) is True
    assert q.push(_lead("Investigate X")) is False     # case-insensitive dedup
    assert q.push(_lead("  investigate x  ")) is False  # whitespace-insensitive
    # Pop the one we put in; pushing the same description AGAIN must still dedup.
    q.pop()
    assert q.push(_lead("investigate X")) is False, (
        "Once a lead has been seen, it stays seen — popping doesn't re-open the dedup slot. "
        "Otherwise a verifier proposing a previously-processed lead would re-run it."
    )


def test_leadqueue_empty_after_drain():
    q = LeadQueue()
    q.push(_lead("only one"))
    assert not q.empty()
    q.pop()
    assert q.empty()


def test_finding_requires_at_least_one_source():
    """Findings without evidence are rejected at construction time —
    a synthesizer must never produce uncited claims."""
    with pytest.raises(ValueError):
        Finding(
            id="f-1",
            claim="subject likes pizza",
            evidence=[],
            confidence="medium",
            lead_id="l-test",
            tags=[],
        )
