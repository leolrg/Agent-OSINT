from osint.agents.react_v1.prompts import (
    build_deepen_prompt,
    build_system_prompt,
    build_synthesis_prompt,
    format_tool_calls_for_synthesis,
    parse_report,
)


def test_system_prompt_contains_subject_and_tools():
    p = build_system_prompt(
        subject="Jane, NYC, @jdoe",
        tool_names=["web_search", "maigret"],
    )
    assert "Jane, NYC, @jdoe" in p
    assert "web_search" in p
    assert "maigret" in p
    assert "extracted_identifiers" in p
    assert "```json" in p


def test_system_prompt_routes_x_content_to_apify_twitter_when_enabled():
    p = build_system_prompt(
        subject="Jane",
        tool_names=["web_search", "apify_twitter"],
    )
    assert "apify_twitter" in p
    assert "X (Twitter)" in p or "X content" in p or "X-native" in p


def test_system_prompt_omits_x_routing_when_apify_twitter_disabled():
    p = build_system_prompt(subject="Jane", tool_names=["web_search", "maigret"])
    assert "apify_twitter" not in p


def test_synthesis_prompt_mentions_stop_reason():
    p = build_synthesis_prompt(stop_reason="budget")
    assert "budget" in p.lower()


def test_system_prompt_pushes_extract_after_search():
    """Regression: the prompt must reference both tools and the search→extract
    workflow. Wording is owned by the user; just assert structure."""
    p = build_system_prompt(
        subject="Jane",
        tool_names=["web_search", "web_extract"],
    )
    assert "web_extract" in p
    assert "web_search" in p
    # The search-and-extract pattern block must exist (in some form).
    lower = p.lower()
    assert "search-and-extract" in lower or "search and extract" in lower
    # The prompt must mention that snippets aren't enough on their own.
    assert "snippet" in lower


def test_system_prompt_uses_prose_plus_tail_json_format():
    """Path 2 contract: agent emits prose, then a fenced JSON tail with
    ONLY extracted_identifiers. The prompt must NOT instruct the agent
    to wrap the report itself in JSON (the old envelope contract)."""
    p = build_system_prompt(
        subject="Jane",
        tool_names=["web_search"],
    )
    lower = p.lower()
    # Prose is the report.
    assert "prose is the report" in lower or "prose report" in lower
    # And the JSON tail carries identifiers only.
    assert "extracted_identifiers" in p
    # The old "report" key in the envelope should NOT be advertised as the
    # agent's output schema (we removed it).
    assert '"report":' not in p


def test_web_extract_routing_rule_warns_about_blocked_origins():
    """Regression: the prompt must tell the agent NOT to pass LinkedIn /
    Instagram / X URLs to web_extract — the website-content-crawler gets
    403 on those, which previously caused the agent to import empty content
    as if it were the subject's profile."""
    p = build_system_prompt(
        subject="Jane",
        tool_names=["web_extract", "apify_linkedin", "apify_instagram", "apify_twitter"],
    )
    lower = p.lower()
    # Each blocked domain must be mentioned in the routing rule.
    for domain in ("linkedin.com", "instagram.com"):
        assert domain in lower, f"prompt should warn about {domain} for web_extract"
    # And it should explicitly route to the apify_* alternatives.
    assert "apify_linkedin" in p
    assert "apify_instagram" in p


def test_system_prompt_requires_source_citations():
    """Reports must cite the tool call that produced each major claim,
    so a reader can audit which evidence supports which finding."""
    p = build_system_prompt(subject="Jane", tool_names=["web_search"])
    lower = p.lower()
    assert "cite" in lower
    assert "tool call" in lower or "tool_call" in lower


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


def test_parse_report_prose_plus_tail_identifiers_json():
    """Path 2: the new contract — prose body, fenced JSON tail with only
    extracted_identifiers. Identifiers come from the JSON; the prose
    (with the JSON block stripped) becomes report['text']."""
    text = (
        "**Executive Summary**\n\n"
        "Jane Doe is a software engineer based in NYC...\n\n"
        "**Sources**\n- web_extract of https://example.com/jane\n\n"
        "```json\n"
        '{"extracted_identifiers": {"emails": ["jane@example.com"], "urls": ["https://example.com/jane"]}}\n'
        "```\n"
    )
    r = parse_report(text)
    assert r["extracted_identifiers"] == {
        "emails": ["jane@example.com"],
        "urls": ["https://example.com/jane"],
    }
    # Prose is preserved (with JSON block removed) under report['text'].
    assert "Executive Summary" in r["report"]["text"]
    assert "Sources" in r["report"]["text"]
    # The JSON block itself was stripped.
    assert "```json" not in r["report"]["text"]
    assert "extracted_identifiers" not in r["report"]["text"]


def test_build_deepen_prompt_embeds_previous_report_and_pass_numbers():
    """The deepen prompt must include (a) the previous pass's report text
    so the LLM can critique it, (b) explicit pass-N-of-N labelling, (c)
    the same routing guidance the system prompt uses, and (d) the prior-
    pass tool-call summary so the agent doesn't retread the same searches."""
    p = build_deepen_prompt(
        subject="Jane Doe, NYC",
        tool_names=["web_search", "web_extract", "apify_linkedin"],
        previous_report_text="**Executive Summary**\nJane is a SWE...",
        previous_tool_calls_summary='1. web_search({"query":"Jane Doe NYC"}) → {"results":[]}',
        pass_num=2,
        total_passes=3,
    )
    # Pass labelling
    assert "pass 2 of 3" in p
    assert "PASS 1" in p or "pass 1" in p.lower()
    # Previous report is embedded verbatim
    assert "Jane is a SWE" in p
    # Prior tool-call summary is embedded verbatim
    assert '1. web_search({"query":"Jane Doe NYC"})' in p
    assert "do not repeat" in p.lower() or "avoid retreading" in p.lower() or "find new" in p.lower()
    # Subject is carried through
    assert "Jane Doe, NYC" in p
    # Tool list + routing guidance carried over
    assert "web_search" in p
    assert "apify_linkedin" in p
    # Critique-then-extend framing is present
    lower = p.lower()
    assert "gap" in lower
    assert "extend" in lower or "new evidence" in lower
    # Output contract still mentions the JSON-tail with extracted_identifiers
    assert "extracted_identifiers" in p


def test_build_deepen_prompt_handles_missing_previous_report_and_calls():
    """If the previous report's text or tool-call summary was empty, the
    prompt should still render without raising and include placeholders."""
    p = build_deepen_prompt(
        subject="Jane",
        tool_names=["web_search"],
        previous_report_text="",
        previous_tool_calls_summary="",
        pass_num=2,
        total_passes=2,
    )
    assert "no draft text available" in p
    assert "no prior tool calls" in p


def test_parse_report_pure_prose_no_json():
    """No fenced JSON anywhere → the whole text becomes report['text'],
    identifiers stay empty."""
    text = "**Executive Summary**\n\nNot much was found about this subject."
    r = parse_report(text)
    assert r["extracted_identifiers"] == {}
    assert r["report"] == {"text": text}


def test_synthesis_prompt_with_tool_calls_summary():
    p = build_synthesis_prompt("budget", '1. web_search({"q":"x"}) → {"r":1}')
    assert "1. web_search" in p
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
        ToolCallRecord(turn=1, tool="web_search", tool_call_id=None,
                       input={"query": "x"}, output={"results": [1, 2, 3]},
                       raw=None, started_at=now, completed_at=now, cost_usd=0.008),
        ToolCallRecord(turn=2, tool="maigret", tool_call_id=None,
                       input={"username": "jdoe"}, output=None, raw=None,
                       started_at=now, completed_at=now, cost_usd=0.0,
                       error="RuntimeError: boom"),
    ]
    out = format_tool_calls_for_synthesis(calls)
    assert "1. web_search" in out
    assert "2. maigret" in out
    assert "ERROR: RuntimeError: boom" in out
