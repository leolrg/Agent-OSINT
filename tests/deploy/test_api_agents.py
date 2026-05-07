"""GET /api/agents returns the catalog of all agent manifests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from osint.api.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", "test-secret-padding-padding-padding-padding")
    return TestClient(create_app())


def test_lists_all_agents(client):
    r = client.get("/api/agents")
    assert r.status_code == 200
    body = r.json()
    assert "agents" in body
    names = sorted(a["name"] for a in body["agents"])
    assert names == ["critic_react_v3", "leadqueue_v2", "react_v1", "xai_multiagent_v1"]


def test_each_entry_has_display_name_description_params(client):
    body = client.get("/api/agents").json()
    for a in body["agents"]:
        assert isinstance(a["display_name"], str) and a["display_name"]
        assert isinstance(a["description"], str) and a["description"]
        assert isinstance(a["estimated_duration"], str)
        assert isinstance(a["params"], list)


def test_includes_common_params_separately(client):
    body = client.get("/api/agents").json()
    assert "common_params" in body
    common_names = sorted(p["name"] for p in body["common_params"])
    assert common_names == ["budget_usd", "max_tool_calls", "max_wall_clock_sec"]


def test_critic_manifest_has_preset_options(client):
    body = client.get("/api/agents").json()
    critic = next(a for a in body["agents"] if a["name"] == "critic_react_v3")
    preset = next(p for p in critic["params"] if p["name"] == "preset")
    assert preset["type"] == "select"
    assert "general" in preset["options"]
    assert "coffee_career" in preset["options"]


def test_no_auth_required(client):
    # Catalog is non-sensitive metadata; endpoint accessible without a JWT.
    r = client.get("/api/agents")
    assert r.status_code == 200
