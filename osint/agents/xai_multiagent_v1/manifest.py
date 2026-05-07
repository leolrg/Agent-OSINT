"""User-facing param schema for the Multi-agent (Grok) agent."""
from osint.agents.base import AgentManifest


MANIFEST = AgentManifest(
    name="xai_multiagent_v1",
    display_name="Multi-agent",
    description="Grok 4.20 multi-agent. First-class X (Twitter) coverage via xAI's native x_search.",
    estimated_duration="~5-15 min",
    params=[],  # No agent-specific knobs; only common params apply.
)
