"""Manifests are valid: names match AGENTS registry, params are real ScanConfig fields."""
from __future__ import annotations

import importlib

import pytest

from osint.agents import AGENTS
from osint.agents.base import AgentManifest, COMMON_PARAMS
from osint.types import ScanConfig


AGENT_NAMES = sorted(AGENTS.keys())


def _load_manifest(name: str) -> AgentManifest:
    mod = importlib.import_module(f"osint.agents.{name}.manifest")
    return mod.MANIFEST


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_manifest_loads_and_name_matches(name):
    m = _load_manifest(name)
    assert isinstance(m, AgentManifest)
    assert m.name == name


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_manifest_params_are_real_scanconfig_fields(name):
    m = _load_manifest(name)
    config_fields = set(ScanConfig.model_fields.keys())
    common = {p.name for p in COMMON_PARAMS}
    for p in m.params:
        # Either it's a real ScanConfig field, or it's a CLI-routed param
        # like 'subject' or 'goal' that isn't on ScanConfig directly.
        # 'subject' is the only allowed exception (passed separately to scan()).
        if p.name == "subject":
            continue
        assert p.name in config_fields or p.name in common, (
            f"Manifest for {name!r} declares param {p.name!r} which is not "
            f"a field on ScanConfig. ScanConfig fields: {sorted(config_fields)}"
        )


def test_all_agents_have_manifests():
    for name in AGENT_NAMES:
        try:
            _load_manifest(name)
        except ImportError:
            pytest.fail(f"Agent {name} is in AGENTS registry but has no manifest.py")
