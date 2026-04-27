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
