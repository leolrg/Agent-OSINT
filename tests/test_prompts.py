from osint.prompts import build_system_prompt, build_synthesis_prompt, parse_report


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
