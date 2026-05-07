from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from osint.state import ScanState


class LLMCostCallback(AsyncCallbackHandler):
    """Accumulate LLM token usage into a ScanState.

    Tries two places for token counts, in order:
    1. `response.llm_output["token_usage"]` — the OpenAI/xAI-compatible shape.
    2. Per-generation `message.usage_metadata` — the LangChain-standardized shape.
    """

    def __init__(self, state: ScanState):
        self.state = state

    async def on_llm_end(self, response: LLMResult, **_: Any) -> None:
        usage = (response.llm_output or {}).get("token_usage") or {}
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0

        if not prompt and not completion:
            for gens in response.generations:
                for gen in gens:
                    msg = getattr(gen, "message", None)
                    meta = getattr(msg, "usage_metadata", None) if msg is not None else None
                    if meta:
                        prompt += meta.get("input_tokens", 0) or 0
                        completion += meta.get("output_tokens", 0) or 0

        if prompt or completion:
            self.state.record_llm_usage(
                input_tokens=int(prompt or 0),
                output_tokens=int(completion or 0),
            )
