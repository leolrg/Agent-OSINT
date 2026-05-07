from unittest.mock import AsyncMock, MagicMock

from osint.tools.tavily import TavilyExtractTool, TavilySearchTool


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_client(payload):
    client = MagicMock()
    client.post = AsyncMock(return_value=_Response(payload))
    return client


async def test_tavily_search_posts_expected_request_and_maps_results():
    payload = {
        "query": "Jane Doe",
        "results": [
            {
                "url": "https://example.com/jane",
                "title": "Jane",
                "content": "Profile snippet",
                "score": 0.91,
            }
        ],
        "usage": {"credits": 1},
    }
    client = _fake_client(payload)
    tool = TavilySearchTool(client=client, api_key="tvly-k")

    content, artifact = await tool._arun(query="Jane Doe", max_results=30)

    assert client.post.call_args.args == ("/search",)
    assert client.post.call_args.kwargs["headers"] == {"Authorization": "Bearer tvly-k"}
    assert client.post.call_args.kwargs["json"] == {
        "query": "Jane Doe",
        "max_results": 20,
        "search_depth": "advanced",
        "include_answer": False,
        "include_raw_content": False,
        "include_usage": True,
    }
    assert artifact["results"] == [
        {
            "url": "https://example.com/jane",
            "title": "Jane",
            "content": "Profile snippet",
            "position": 1,
            "score": 0.91,
        }
    ]
    assert '"Jane Doe"' in content


async def test_tavily_extract_posts_expected_request_and_maps_results():
    payload = {
        "results": [
            {
                "url": "https://example.com/jane",
                "raw_content": "# Jane\nProfile body",
            }
        ],
        "failed_results": [],
        "usage": {"credits": 1},
    }
    client = _fake_client(payload)
    tool = TavilyExtractTool(client=client, api_key="tvly-k")

    content, artifact = await tool._arun(urls=["https://example.com/jane"])

    assert client.post.call_args.args == ("/extract",)
    assert client.post.call_args.kwargs["headers"] == {"Authorization": "Bearer tvly-k"}
    assert client.post.call_args.kwargs["json"] == {
        "urls": ["https://example.com/jane"],
        "extract_depth": "advanced",
        "format": "markdown",
        "include_images": False,
        "include_usage": True,
    }
    assert artifact["results"] == [
        {"url": "https://example.com/jane", "raw_content": "# Jane\nProfile body"}
    ]
    assert artifact["failed_results"] == []
    assert "# Jane" in content
