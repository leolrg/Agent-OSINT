"""Agent runner registry. Map agent_version (str) → AgentRunner class."""
from osint.agents.react_v1 import ReactV1Runner

AGENTS = {
    "react_v1": ReactV1Runner,
}
