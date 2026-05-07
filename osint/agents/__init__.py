"""Agent runner registry. Map agent_version (str) → AgentRunner class."""
from osint.agents.critic_react_v3 import CriticReactV3Runner
from osint.agents.leadqueue_v2 import LeadQueueV2Runner
from osint.agents.react_v1 import ReactV1Runner
from osint.agents.xai_multiagent_v1 import XaiMultiAgentV1Runner

AGENTS = {
    "react_v1": ReactV1Runner,
    "leadqueue_v2": LeadQueueV2Runner,
    "xai_multiagent_v1": XaiMultiAgentV1Runner,
    "critic_react_v3": CriticReactV3Runner,
}
