"""Per-tool label + arg rendering."""
from __future__ import annotations

from osint.worker.tool_labels import describe_tool_call


def test_web_search():
    label, arg = describe_tool_call(
        "web_search", {"query": "Jane Doe transformer", "max_results": 7}
    )
    assert label == "Web search"
    assert arg == '"Jane Doe transformer"'


def test_web_extract_single_url():
    label, arg = describe_tool_call(
        "web_extract", {"urls": ["https://news.ycombinator.com/item?id=1"]}
    )
    assert label == "Read page"
    assert "news.ycombinator.com" in arg
    assert "https://" not in arg  # domain-only, no protocol


def test_web_extract_multiple_urls():
    label, arg = describe_tool_call(
        "web_extract",
        {"urls": [
            "https://github.com/x", "https://arxiv.org/y",
            "https://news.ycombinator.com/z",
        ]},
    )
    assert label == "Read pages"  # plural
    assert "github.com" in arg
    assert "arxiv.org" in arg
    assert "+ 1 more" in arg


def test_apify_linkedin_extracts_slug():
    label, arg = describe_tool_call(
        "apify_linkedin",
        {"profile_url": "https://www.linkedin.com/in/jane-doe-89a/"},
    )
    assert label == "LinkedIn"
    assert arg == "jane-doe-89a"


def test_apify_instagram():
    label, arg = describe_tool_call(
        "apify_instagram", {"username": "janedoe.eth", "results_limit": 20}
    )
    assert label == "Instagram"
    assert arg == "janedoe.eth"


def test_apify_twitter_handle_mode():
    label, arg = describe_tool_call(
        "apify_twitter", {"handle": "janedoe_ml", "max_items": 20}
    )
    assert label == "X / Twitter"
    assert arg == "@janedoe_ml"


def test_apify_twitter_search_mode():
    label, arg = describe_tool_call(
        "apify_twitter",
        {"search_query": '"jane doe" since:2025-06', "max_items": 20},
    )
    assert label == "X / Twitter search"
    assert arg == '"jane doe" since:2025-06'


def test_maigret():
    label, arg = describe_tool_call("maigret", {"username": "janedoe"})
    assert label == "Username search"
    assert arg == "janedoe"


def test_unknown_tool_falls_through():
    label, arg = describe_tool_call("internal_secret_tool", {"foo": "bar"})
    assert label == "Tool"
    assert arg == ""


def test_missing_args_does_not_raise():
    label, arg = describe_tool_call("web_search", {})
    assert label == "Web search"
    assert arg == ""  # graceful — no crash on bad/missing args
