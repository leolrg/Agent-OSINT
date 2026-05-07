from __future__ import annotations

from datetime import datetime, timezone

from osint.api.routes.scans import build_steps_from_events


def test_build_steps_from_finished_tool_events():
    steps = build_steps_from_events(
        [
            {"event": "scan.started", "ts": 1000.0},
            {
                "event": "tool.started",
                "ts": 1001.0,
                "tool_name": "web_search",
                "display_label": "Web search",
                "arg_summary": '"Jane Doe"',
                "args": {"query": "Jane Doe"},
            },
            {
                "event": "tool.finished",
                "ts": 1004.0,
                "tool_name": "web_search",
                "display_label": "Web search",
                "arg_summary": '"Jane Doe"',
                "args": {"query": "Jane Doe"},
                "result_count": 3,
                "result_size_bytes": 1280,
            },
        ],
        started_at=datetime.fromtimestamp(1000.0, tz=timezone.utc),
    )

    assert steps == [
        {
            "ts": 4,
            "displayLabel": "Web search",
            "argSummary": '"Jane Doe"',
            "fullArgs": {"query": "Jane Doe"},
            "responsePreview": "3 results\n1280 bytes",
        },
    ]


def test_build_steps_keeps_pending_started_tool():
    steps = build_steps_from_events(
        [
            {
                "event": "tool.started",
                "ts": 20.0,
                "tool": "maigret",
                "args": {"username": "jdoe"},
            },
        ],
    )

    assert steps == [
        {
            "ts": 0,
            "displayLabel": "maigret",
            "argSummary": "",
            "fullArgs": {"username": "jdoe"},
            "responsePreview": "Still running",
        },
    ]
