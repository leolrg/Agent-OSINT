import pytest

from osint.agents.critic_react_v3.prompts import PRESETS, PRESET_HINTS


def test_presets_cover_all_six_names():
    assert set(PRESETS) == {
        "coffee_career", "coffee_personal", "reconnect",
        "sales_outreach", "dossier", "general",
    }


def test_preset_hints_cover_all_six_names():
    assert set(PRESET_HINTS) == set(PRESETS)


@pytest.mark.parametrize("name", [
    "coffee_career", "coffee_personal", "reconnect",
    "sales_outreach", "dossier", "general",
])
def test_preset_preamble_is_nonempty_string(name):
    assert isinstance(PRESETS[name], str) and PRESETS[name].strip()


@pytest.mark.parametrize("name", [
    "coffee_career", "coffee_personal", "reconnect",
    "sales_outreach", "dossier", "general",
])
def test_preset_hint_is_short_one_liner(name):
    assert isinstance(PRESET_HINTS[name], str) and PRESET_HINTS[name].strip()
    assert len(PRESET_HINTS[name]) < 200


from osint.agents.critic_react_v3.prompts import build_system_prompt


def test_build_system_prompt_contains_subject_and_preset_preamble():
    p = build_system_prompt(
        subject="Jane Doe",
        goal="",
        preset="coffee_career",
        tool_names=["web_search", "web_extract"],
    )
    assert "Jane Doe" in p
    assert "coffee chat" in p.lower()
    assert "web_search" in p
    assert "web_extract" in p


def test_build_system_prompt_appends_goal_text_after_preset():
    p = build_system_prompt(
        subject="Jane Doe",
        goal="Meeting to discuss her transformer-inference work",
        preset="coffee_career",
        tool_names=["web_search"],
    )
    assert "transformer-inference" in p
    coffee_idx = p.lower().find("coffee chat")
    goal_idx = p.find("transformer-inference")
    assert coffee_idx < goal_idx, "goal text must appear after preset preamble"


def test_build_system_prompt_general_with_empty_goal_still_valid():
    p = build_system_prompt(
        subject="Jane Doe",
        goal="",
        preset="general",
        tool_names=["web_search"],
    )
    assert "Jane Doe" in p
    assert "Investigate" in p


def test_build_system_prompt_states_parallelism_rule():
    p = build_system_prompt(
        subject="Jane Doe", goal="", preset="general",
        tool_names=["web_search"],
    )
    assert "parallel" in p.lower() or "batch" in p.lower()


def test_build_system_prompt_states_ledger_rule():
    p = build_system_prompt(
        subject="Jane Doe", goal="", preset="general",
        tool_names=["web_search"],
    )
    assert ("open" in p.lower() and "ledger" in p.lower()) or '"open"' in p


def test_build_system_prompt_states_final_report_envelope():
    p = build_system_prompt(
        subject="Jane Doe", goal="", preset="general",
        tool_names=["web_search"],
    )
    assert "extracted_identifiers" in p


def test_build_system_prompt_borrows_react_v1_report_format():
    p = build_system_prompt(
        subject="Jane Doe",
        goal="",
        preset="dossier",
        tool_names=["web_search", "web_extract"],
    )
    for heading in [
        "**Executive Summary**",
        "**Identified Name Variations & Aliases**",
        "**Comprehensive Profile**",
        "**Digital & Social Media Footprint**",
        "**Key Associates & Network Map**",
        "**Timeline of Significant Events**",
        "**Hypotheses, Patterns & Potential Red Flags**",
        "**Leads for Further Investigation**",
        "**Sources**",
        "**Overall Assessment**",
    ]:
        assert heading in p
    assert "The prose IS the report" in p


def test_build_system_prompt_pushes_deeper_investigation():
    p = build_system_prompt(
        subject="Jane Doe",
        goal="",
        preset="general",
        tool_names=["web_search", "web_extract", "apify_twitter"],
    )
    lower = p.lower()
    assert "treat those keywords only as initial seeds" in lower
    assert "every new piece of information must generate" in lower
    assert "at least 15 distinct web_search queries" in lower
    assert "at least 5 web_extract calls" in lower
    assert "2 cross-reference points" in lower


from osint.agents.critic_react_v3.prompts import Ledger, parse_ledger


def test_parse_ledger_well_formed():
    text = (
        "```json\n"
        '{"open": ["Q1", "Q2"], "answered": ["A1"], "dropped": []}\n'
        "```\nThe rest of the report."
    )
    led = parse_ledger(text)
    assert led.open == ["Q1", "Q2"]
    assert led.answered == ["A1"]
    assert led.dropped == []


def test_parse_ledger_empty_lists_when_keys_missing():
    text = '```json\n{"open": []}\n```'
    led = parse_ledger(text)
    assert led.open == []
    assert led.answered == []
    assert led.dropped == []


def test_parse_ledger_no_block_returns_empty_ledger():
    led = parse_ledger("No JSON block at all in this text.")
    assert led.open == []
    assert led.answered == []
    assert led.dropped == []


def test_parse_ledger_malformed_json_returns_empty_ledger():
    text = '```json\n{"open": [bad}\n```'
    led = parse_ledger(text)
    assert led.open == []
    assert led.answered == []
    assert led.dropped == []


def test_parse_ledger_picks_first_json_block_only():
    """The first JSON block IS the ledger; later blocks (e.g. extracted_identifiers)
    must not be parsed as a ledger."""
    text = (
        '```json\n{"open": ["Q1"]}\n```\n'
        'Some prose.\n'
        '```json\n{"extracted_identifiers": {"emails": []}}\n```'
    )
    led = parse_ledger(text)
    assert led.open == ["Q1"]
