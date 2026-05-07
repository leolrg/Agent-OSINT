import pytest
from unittest.mock import AsyncMock, MagicMock

from osint.tools.apify import (
    ApifyInstagramTool,
    ApifyLinkedInTool,
    ApifyTwitterTool,
)


def _fake_client(items):
    """Mock chain. The real apify-client's actor.call returns the raw API JSON
    which uses camelCase, so the dataset id key is `defaultDatasetId`."""
    client = MagicMock()
    actor = MagicMock()
    dataset = MagicMock()
    client.actor = MagicMock(return_value=actor)
    client.dataset = MagicMock(return_value=dataset)
    actor.call = AsyncMock(return_value={"defaultDatasetId": "ds1"})
    dataset.list_items = AsyncMock(return_value=MagicMock(items=items))
    return client, actor, dataset


async def test_apify_instagram_runs_actor():
    client, actor, dataset = _fake_client([{"username": "jdoe"}])
    tool = ApifyInstagramTool(client=client, actor_id="apify~instagram-scraper")
    content, artifact = await tool._arun(username="jdoe")
    actor.call.assert_awaited_once()
    # We pass `directUrls` (NOT `usernames`) because the actor's usernames
    # input silently drops dotted handles like `simonwen.eth`. Confirmed
    # live against apify/instagram-scraper, 2026-04.
    run_input = actor.call.call_args.kwargs["run_input"]
    assert run_input["directUrls"] == ["https://www.instagram.com/jdoe/"]
    assert run_input["resultsType"] == "details"
    assert "usernames" not in run_input
    assert artifact["items"][0]["username"] == "jdoe"
    assert "jdoe" in content


async def test_apify_instagram_handles_dotted_username():
    """Regression for the actor's `usernames`-handling bug: dotted handles
    like `simonwen.eth` must flow through directUrls so the actor doesn't
    return its no_items placeholder."""
    client, actor, dataset = _fake_client(
        [{"username": "simonwen.eth", "biography": "gz | nyc", "followersCount": 2323}]
    )
    tool = ApifyInstagramTool(client=client, actor_id="apify~instagram-scraper")
    content, artifact = await tool._arun(username="simonwen.eth")
    run_input = actor.call.call_args.kwargs["run_input"]
    assert run_input["directUrls"] == ["https://www.instagram.com/simonwen.eth/"]
    assert artifact["items"][0]["biography"] == "gz | nyc"


async def test_apify_linkedin_runs_actor():
    client, actor, dataset = _fake_client([{"fullName": "Jane"}])
    tool = ApifyLinkedInTool(client=client, actor_id="apify~linkedin-profile-scraper")
    content, artifact = await tool._arun(profile_url="https://www.linkedin.com/in/jane/")
    run_input = actor.call.call_args.kwargs["run_input"]
    assert any("linkedin.com/in/jane" in str(v) for v in run_input.values())
    assert artifact["items"][0]["fullName"] == "Jane"


async def test_apify_twitter_handle_mode_runs_actor():
    client, actor, dataset = _fake_client(
        [{"full_text": "hi", "url": "https://x.com/jdoe/status/1"}]
    )
    tool = ApifyTwitterTool(client=client, actor_id="gentle_cloud~twitter-tweets-scraper")
    content, artifact = await tool._arun(handle="jdoe", max_items=25)
    run_input = actor.call.call_args.kwargs["run_input"]
    # gentle_cloud uses start_urls + since_date + result_count. Without
    # since_date the actor's filter drops every tweet and the dataset comes
    # back as simulation placeholders. result_count is string-typed per the
    # actor schema. Verified live 2026-04 against @semona0x.
    assert run_input["start_urls"] == [{"url": "https://x.com/jdoe"}]
    assert run_input["result_count"] == "25"
    assert run_input["since_date"]  # required to avoid simulation fallback
    assert artifact["items"][0]["full_text"] == "hi"


async def test_apify_twitter_search_mode_runs_actor():
    client, actor, dataset = _fake_client([{"full_text": "hello"}])
    tool = ApifyTwitterTool(client=client, actor_id="gentle_cloud~twitter-tweets-scraper")
    await tool._arun(search_query="jane doe", max_items=10)
    run_input = actor.call.call_args.kwargs["run_input"]
    # Search mode is best-effort: gentle_cloud has no first-class search
    # parameter, so we route to x.com/search?q=... as a profile URL. Works
    # for keyword and `from:<handle>` queries; advanced operators are flaky.
    assert run_input["start_urls"][0]["url"].startswith("https://x.com/search?q=")
    assert "jane doe" in run_input["start_urls"][0]["url"]
    assert run_input["result_count"] == "10"


async def test_apify_twitter_requires_handle_or_query():
    tool = ApifyTwitterTool(client=MagicMock(), actor_id="x")
    with pytest.raises(ValueError):
        await tool._arun()


async def test_actors_have_per_actor_timeouts():
    """Every actor.call must pass a `timeout_secs` so a stuck actor can't
    block the scan for its full server-side default (often 1 hour).
    Specifically `gentle_cloud~twitter-tweets-scraper` previously timed
    out at 60 minutes when its shared X cookie pool ran dry — a per-call
    cap of 120s makes that mode-of-failure cheap."""
    from osint.tools.apify import (
        _ACTOR_TIMEOUT_SECS,
        DEFAULT_GOOGLE_SEARCH_ACTOR,
        DEFAULT_IG_ACTOR,
        DEFAULT_LI_ACTOR,
        DEFAULT_TW_ACTOR,
        DEFAULT_WEB_CRAWLER_ACTOR,
    )
    # Every default actor must have a timeout configured.
    for actor in (
        DEFAULT_GOOGLE_SEARCH_ACTOR,
        DEFAULT_WEB_CRAWLER_ACTOR,
        DEFAULT_IG_ACTOR,
        DEFAULT_LI_ACTOR,
        DEFAULT_TW_ACTOR,
    ):
        assert actor in _ACTOR_TIMEOUT_SECS, f"{actor} missing from _ACTOR_TIMEOUT_SECS"
    # Twitter must be tightly bounded — that's the whole point of this gate.
    assert _ACTOR_TIMEOUT_SECS[DEFAULT_TW_ACTOR] <= 180, (
        "Twitter actor timeout must stay <= 3 min so a dead cookie pool "
        "can't burn 60 min of wall clock per call."
    )

    # Each Apify-backed tool must actually pass its actor's timeout into
    # actor.call(timeout_secs=...) — verified by inspecting the mock call.
    for ToolCls, run_kwargs, expected_actor in (
        (ApifyInstagramTool, {"username": "x"}, DEFAULT_IG_ACTOR),
        (ApifyTwitterTool, {"handle": "x"}, DEFAULT_TW_ACTOR),
    ):
        client, actor_mock, _ = _fake_client([{"x": 1}])
        tool = ToolCls(client=client)
        await tool._arun(**run_kwargs)
        kwargs = actor_mock.call.call_args.kwargs
        assert kwargs.get("timeout_secs") == _ACTOR_TIMEOUT_SECS[expected_actor], (
            f"{ToolCls.__name__} did not forward timeout_secs to actor.call"
        )


async def test_apify_metadata():
    ig = ApifyInstagramTool(client=MagicMock(), actor_id="x")
    li = ApifyLinkedInTool(client=MagicMock(), actor_id="x")
    tw = ApifyTwitterTool(client=MagicMock(), actor_id="x")
    assert ig.name == "apify_instagram"
    assert li.name == "apify_linkedin"
    assert tw.name == "apify_twitter"
    assert all(t.response_format == "content_and_artifact" for t in (ig, li, tw))


def test_apify_client_real_api_compatibility():
    """Contract test (no network): assert ApifyClientAsync's actor.call /
    dataset.list_items have the kwargs we depend on. Catches API drift if
    apify-client bumps a major version."""
    import inspect
    from apify_client import ApifyClientAsync

    client = ApifyClientAsync(token="dummy")
    actor_call = client.actor("x").call
    dataset_list = client.dataset("x").list_items
    assert inspect.iscoroutinefunction(actor_call), "actor.call must be async"
    assert inspect.iscoroutinefunction(dataset_list), "dataset.list_items must be async"
    assert "run_input" in inspect.signature(actor_call).parameters
