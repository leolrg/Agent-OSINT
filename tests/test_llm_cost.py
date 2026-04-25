from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from osint.llm_cost import LLMCostCallback
from osint.state import ScanState
from osint.types import ScanConfig


def _llm_result_with_token_usage(prompt: int, completion: int) -> LLMResult:
    ai = AIMessage(content="ok")
    gen = ChatGeneration(message=ai)
    return LLMResult(
        generations=[[gen]],
        llm_output={"token_usage": {"prompt_tokens": prompt, "completion_tokens": completion}},
    )


def _llm_result_with_usage_metadata(inp: int, out: int) -> LLMResult:
    ai = AIMessage(content="ok",
                   usage_metadata={"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out})
    gen = ChatGeneration(message=ai)
    return LLMResult(generations=[[gen]])


async def test_callback_picks_up_openai_style_token_usage():
    state = ScanState(scan_id="x", subject="S", config=ScanConfig())
    cb = LLMCostCallback(state)
    await cb.on_llm_end(_llm_result_with_token_usage(prompt=1_000, completion=500))
    assert state.llm_input_tokens == 1_000
    assert state.llm_output_tokens == 500


async def test_callback_picks_up_usage_metadata_fallback():
    state = ScanState(scan_id="x", subject="S", config=ScanConfig())
    cb = LLMCostCallback(state)
    await cb.on_llm_end(_llm_result_with_usage_metadata(inp=2_000, out=250))
    assert state.llm_input_tokens == 2_000
    assert state.llm_output_tokens == 250


async def test_callback_is_silent_when_usage_is_missing():
    state = ScanState(scan_id="x", subject="S", config=ScanConfig())
    cb = LLMCostCallback(state)
    await cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]]))
    assert state.llm_input_tokens == 0
    assert state.llm_output_tokens == 0
