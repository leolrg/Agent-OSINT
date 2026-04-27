from osint.agents.critic_react_v3.critic import Verdict, parse_critic_verdict


def test_accept_verdict():
    v = parse_critic_verdict("VERDICT: ACCEPT\n")
    assert v.accept is True
    assert v.gaps == []


def test_reject_with_bullets():
    text = (
        "VERDICT: REJECT\n"
        "GAPS:\n"
        "- Subject's current employer not confirmed\n"
        "- Email fc202817@bunka-fc.ac.jp never followed up via web_search\n"
    )
    v = parse_critic_verdict(text)
    assert v.accept is False
    assert v.gaps == [
        "Subject's current employer not confirmed",
        "Email fc202817@bunka-fc.ac.jp never followed up via web_search",
    ]


def test_reject_without_bullets_still_rejected_but_empty_gaps():
    v = parse_critic_verdict("VERDICT: REJECT\n")
    assert v.accept is False
    assert v.gaps == []


def test_malformed_treated_as_accept():
    v = parse_critic_verdict("nonsense, no verdict line at all")
    assert v.accept is True
    assert v.gaps == []


def test_verdict_case_insensitive():
    assert parse_critic_verdict("verdict: accept").accept is True
    assert parse_critic_verdict("Verdict: Reject\nGAPS:\n- X").accept is False


def test_reject_with_mixed_bullet_styles():
    text = (
        "VERDICT: REJECT\n"
        "GAPS:\n"
        "* alpha\n"
        "1. beta\n"
        "- gamma\n"
        "• delta\n"
        "2) epsilon\n"
    )
    v = parse_critic_verdict(text)
    assert v.accept is False
    assert v.gaps == ["alpha", "beta", "gamma", "delta", "epsilon"]


def test_reject_stops_collecting_at_next_header():
    text = (
        "VERDICT: REJECT\n"
        "GAPS:\n"
        "- real gap\n"
        "\n"
        "NOTES:\n"
        "- not a gap\n"
    )
    v = parse_critic_verdict(text)
    assert v.accept is False
    assert v.gaps == ["real gap"]
