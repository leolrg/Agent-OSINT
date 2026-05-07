"""User-facing param schema for the ReAct agent."""
from osint.agents.base import AgentManifest, ParamField


MANIFEST = AgentManifest(
    name="react_v1",
    display_name="ReAct",
    description="Single ReAct loop with multi-pass deepen. Fast, modest cost.",
    estimated_duration="~3-10 min",
    params=[
        ParamField(
            name="passes", label="Passes", type="int",
            default=1, min=1, max=5,
            help="How many times the agent re-considers its draft. "
                 "1 = single pass; 2+ = additional 'deepen' passes that "
                 "critique the previous draft.",
        ),
    ],
)
