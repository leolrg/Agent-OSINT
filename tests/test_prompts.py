from osint.prompts import build_system_prompt, build_synthesis_prompt, format_tool_calls_for_synthesis, parse_report


def test_system_prompt_contains_subject_and_tools():
    p = build_system_prompt(
        subject="Jane, NYC, @jdoe",
        tool_names=["tavily_search", "maigret"],
    )
    assert "Jane, NYC, @jdoe" in p
    assert "tavily_search" in p
    assert "maigret" in p
    assert "extracted_identifiers" in p
    assert "```json" in p


def test_system_prompt_routes_x_content_to_apify_twitter_when_enabled():
    p = build_system_prompt(
        subject="Jane",
        tool_names=["tavily_search", "apify_twitter"],
    )
    assert "apify_twitter" in p
    assert "X (Twitter)" in p or "X content" in p or "X-native" in p


def test_system_prompt_omits_x_routing_when_apify_twitter_disabled():
    p = build_system_prompt(subject="Jane", tool_names=["tavily_search", "maigret"])
    assert "apify_twitter" not in p


def test_synthesis_prompt_mentions_stop_reason():
    p = build_synthesis_prompt(stop_reason="budget")
    assert "budget" in p.lower()


def test_system_prompt_pushes_extract_after_search():
    """Regression: the prompt must explicitly tell the agent to call
    tavily_extract after tavily_search. Past behavior was for the LLM
    to skip extract whenever it felt the snippets were good enough."""
    p = build_system_prompt(
        subject="Jane",
        tool_names=["tavily_search", "tavily_extract"],
    )
    assert "tavily_extract" in p
    # The Steps block should call out the search→extract pattern.
    assert "Search-and-extract pattern" in p or "search-and-extract" in p.lower()
    # And the routing rule should be assertive, not optional.
    lower = p.lower()
    assert "snippets are not sufficient" in lower or "snippets" in lower
    # The phrase should appear that ties the two tools together.
    assert "after every tavily_search" in lower or "right after every tavily_search" in lower


def test_parse_report_from_fenced_json():
    text = 'stuff\n```json\n{"extracted_identifiers": {"emails": ["j@e"]}, "report": {"summary": "hi"}}\n```\nmore'
    r = parse_report(text)
    assert r["extracted_identifiers"] == {"emails": ["j@e"]}
    assert r["report"] == {"summary": "hi"}


def test_parse_report_falls_back_on_invalid_json():
    r = parse_report("no json here")
    assert r["extracted_identifiers"] == {}
    assert r["report"] == {"text": "no json here"}


def test_parse_report_handles_bare_json():
    r = parse_report('{"extracted_identifiers": {}, "report": {"x": 1}}')
    assert r["report"] == {"x": 1}


def test_synthesis_prompt_with_tool_calls_summary():
    p = build_synthesis_prompt("budget", '1. tavily_search({"q":"x"}) → {"r":1}')
    assert "1. tavily_search" in p
    assert "budget" in p


def test_synthesis_prompt_default_summary_when_no_tool_calls():
    p = build_synthesis_prompt("max_calls")
    assert "no tool calls" in p
    assert "max_calls" in p


def test_format_tool_calls_for_synthesis():
    from datetime import datetime, timezone
    from osint.types import ToolCallRecord
    now = datetime.now(timezone.utc)
    calls = [
        ToolCallRecord(turn=1, tool="tavily_search", tool_call_id=None,
                       input={"query": "x"}, output={"results": [1, 2, 3]},
                       raw=None, started_at=now, completed_at=now, cost_usd=0.008),
        ToolCallRecord(turn=2, tool="maigret", tool_call_id=None,
                       input={"username": "jdoe"}, output=None, raw=None,
                       started_at=now, completed_at=now, cost_usd=0.0,
                       error="RuntimeError: boom"),
    ]
    out = format_tool_calls_for_synthesis(calls)
    assert "1. tavily_search" in out
    assert "2. maigret" in out
    assert "ERROR: RuntimeError: boom" in out
