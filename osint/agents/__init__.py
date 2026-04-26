"""Agent runner registry. Map agent_version (str) → AgentRunner class."""
from osint.agents.leadqueue_v2 import LeadQueueV2Runner
from osint.agents.react_v1 import ReactV1Runner

AGENTS = {
    "react_v1": ReactV1Runner,
    "leadqueue_v2": LeadQueueV2Runner,
}
