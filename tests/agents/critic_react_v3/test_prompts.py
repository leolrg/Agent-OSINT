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
