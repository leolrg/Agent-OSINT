"""GET /api/agents — public catalog of all agent manifests.

Catalog is non-sensitive: no auth required. Used by the new-scan form
to render agent-specific fields dynamically.
"""
from __future__ import annotations

import importlib

from fastapi import APIRouter

from osint.agents import AGENTS
from osint.agents.base import COMMON_PARAMS

router = APIRouter()


def _load_manifest(name: str):
    return importlib.import_module(f"osint.agents.{name}.manifest").MANIFEST


@router.get("/api/agents")
async def list_agents() -> dict:
    agents = []
    for name in sorted(AGENTS.keys()):
        try:
            m = _load_manifest(name)
            agents.append(m.model_dump(mode="json"))
        except (ImportError, AttributeError):
            # Agent without a manifest is invisible to the UI but still works
            # via the CLI. Emit a stub so the new-scan form doesn't crash.
            agents.append({
                "name": name,
                "display_name": name,
                "description": "(no manifest)",
                "estimated_duration": "",
                "params": [],
            })
    return {
        "agents": agents,
        "common_params": [p.model_dump(mode="json") for p in COMMON_PARAMS],
    }
