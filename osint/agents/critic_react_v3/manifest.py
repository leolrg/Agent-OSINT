"""User-facing param schema for the Critic-driven agent."""
from osint.agents.base import AgentManifest, ParamField


MANIFEST = AgentManifest(
    name="critic_react_v3",
    display_name="Critic",
    description="Single ReAct + open-question ledger + adversarial critic. Goal-conditioned.",
    estimated_duration="variable",
    params=[
        ParamField(
            name="preset", label="Investigation context", type="select",
            default="general",
            options=[
                "coffee_career", "coffee_personal", "reconnect",
                "sales_outreach", "dossier", "general",
            ],
            help="Canned investigation posture combined with your goal in the system prompt.",
        ),
        ParamField(
            name="goal", label="Goal", type="text",
            help="Free-form goal text appended after the preset preamble. Optional.",
        ),
        ParamField(
            name="max_critic_rejections", label="Max critic rejections", type="int",
            default=3, min=1, max=10,
            help="Cap on critic rejection cycles.",
            advanced=True,
        ),
        ParamField(
            name="max_recursion_per_engagement", label="Max recursion per engagement",
            type="int", default=50, min=10, max=200, advanced=True,
        ),
        ParamField(
            name="min_tool_calls", label="Minimum tool calls", type="int",
            default=1, min=0, max=100, advanced=True,
            help="Floor below which the critic's ACCEPT verdict is overridden to REJECT.",
        ),
        ParamField(
            name="min_critic_rejections", label="Minimum critic rejections", type="int",
            default=0, min=0, max=10, advanced=True,
            help="Floor on critic rejection rounds before ACCEPT terminates.",
        ),
    ],
)
