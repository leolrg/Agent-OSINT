"""Smoke test for the AgentRunner Protocol — confirms the module imports
and the symbol exists. Concrete runner conformance is covered in each
agent's own test module."""

def test_agent_runner_protocol_importable():
    from osint.agents.base import AgentRunner
    assert AgentRunner is not None
    # Protocol has the expected method
    assert "run" in AgentRunner.__dict__ or hasattr(AgentRunner, "run")
