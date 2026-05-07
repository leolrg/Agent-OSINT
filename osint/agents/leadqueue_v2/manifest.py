"""User-facing param schema for the Lead Queue agent."""
from osint.agents.base import AgentManifest, ParamField


MANIFEST = AgentManifest(
    name="leadqueue_v2",
    display_name="Lead Queue",
    description="Priority-queue investigation with verifier loop. Slow but thorough.",
    estimated_duration="~30-60 min",
    params=[
        ParamField(
            name="max_processor_tool_calls", label="Tool calls per lead", type="int",
            default=5, min=1, max=20,
            help="Per-lead tool-call ceiling for the processor's mini-ReAct loop.",
            advanced=True,
        ),
        ParamField(
            name="max_verifier_iterations", label="Max verifier rounds", type="int",
            default=3, min=1, max=10,
            help="Cap on verifier→re-investigate cycles after first synthesis.",
            advanced=True,
        ),
    ],
)
