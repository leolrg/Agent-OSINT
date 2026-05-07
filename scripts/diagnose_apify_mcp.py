"""Probe Apify Remote MCP directly to see what tools/list returns.

Mirrors the exact server_url xai_multiagent_v1 sends, with the same
Authorization header, then issues an MCP `tools/list` over Streamable
HTTP and prints what comes back.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from osint.agents.xai_multiagent_v1.runner import (
    APIFY_MCP_ACTORS,
    build_apify_mcp_url,
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


def _post(url: str, headers: dict[str, str], body: dict) -> tuple[int, dict[str, str], str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode("utf-8", "replace")


def _parse_sse(body: str) -> list[dict]:
    """Streamable HTTP MCP can return the JSON-RPC reply as SSE."""
    events: list[dict] = []
    for chunk in body.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return events


def main() -> int:
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("APIFY_TOKEN not set", file=sys.stderr)
        return 2

    use_url_token = os.environ.get("DIAG_USE_URL_TOKEN") == "1"
    bare_auth = os.environ.get("DIAG_BARE_AUTH") == "1"
    url = build_apify_mcp_url()
    if use_url_token:
        url = url + f"&token={token}"
    print("Server URL:")
    safe = url.replace(token, "<APIFY_TOKEN>") if token in url else url
    print(f"  {safe}")
    print(f"  (use_url_token={use_url_token}, bare_auth={bare_auth})")
    print(f"Filter actors ({len(APIFY_MCP_ACTORS)}):")
    for a in APIFY_MCP_ACTORS:
        print(f"  - {a}")
    print()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if not use_url_token:
        # Test bare-token vs Bearer-prefixed auth (mimicking what xAI's
        # `authorization` field might do under the hood).
        headers["Authorization"] = token if bare_auth else f"Bearer {token}"

    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "osint-diag", "version": "0"},
        },
    }
    print("=== initialize ===")
    status, resp_headers, body = _post(url, headers, init_body)
    print(f"HTTP {status}")
    print(f"mcp-session-id: {resp_headers.get('mcp-session-id') or resp_headers.get('Mcp-Session-Id')}")
    print(f"content-type: {resp_headers.get('content-type') or resp_headers.get('Content-Type')}")
    body_preview = body[:600]
    print(f"body[:600]: {body_preview}")
    print()

    session_id = resp_headers.get("mcp-session-id") or resp_headers.get("Mcp-Session-Id")
    if session_id:
        headers["mcp-session-id"] = session_id

    # MCP requires notifications/initialized before further requests.
    print("=== notifications/initialized ===")
    status, _, body = _post(
        url,
        headers,
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    print(f"HTTP {status} body[:200]: {body[:200]}")
    print()

    print("=== tools/list ===")
    status, resp_headers, body = _post(
        url,
        headers,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    print(f"HTTP {status}")
    print(f"content-type: {resp_headers.get('content-type') or resp_headers.get('Content-Type')}")

    payloads: list[dict] = []
    if "event-stream" in (resp_headers.get("content-type") or resp_headers.get("Content-Type") or ""):
        payloads = _parse_sse(body)
    else:
        try:
            payloads = [json.loads(body)]
        except json.JSONDecodeError:
            print(f"body (raw, first 2000):\n{body[:2000]}")
            return 1

    for p in payloads:
        if p.get("error"):
            print(f"JSON-RPC error: {json.dumps(p['error'], indent=2)}")
            continue
        result = p.get("result") or {}
        tools = result.get("tools") or []
        print(f"tools loaded: {len(tools)}")
        for t in tools:
            print(f"  - {t.get('name')!r}")
        if not tools:
            print("(empty list — server returned no tools for this filter)")
            print(f"raw result: {json.dumps(result, indent=2)[:1500]}")

    if os.environ.get("DIAG_TOOLS_CALL") != "1":
        return 0

    print()
    print("=== tools/call apify--instagram-profile-scraper (test args) ===")
    status, resp_headers, body = _post(
        url,
        headers,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "apify--instagram-profile-scraper",
                "arguments": {"usernames": ["instagram"], "resultsLimit": 1},
            },
        },
    )
    print(f"HTTP {status}")
    print(f"content-type: {resp_headers.get('content-type') or resp_headers.get('Content-Type')}")
    if "event-stream" in (resp_headers.get("content-type") or resp_headers.get("Content-Type") or ""):
        for ev in _parse_sse(body):
            print(json.dumps(ev, indent=2)[:2000])
    else:
        print(body[:2000])

    print()
    print("=== tools/call BOGUS-NAME (to see how Apify reports unknown tool) ===")
    status, resp_headers, body = _post(
        url,
        headers,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "definitely-not-a-tool", "arguments": {}},
        },
    )
    print(f"HTTP {status}")
    if "event-stream" in (resp_headers.get("content-type") or resp_headers.get("Content-Type") or ""):
        for ev in _parse_sse(body):
            print(json.dumps(ev, indent=2)[:1500])
    else:
        print(body[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
