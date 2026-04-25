import inspect
from datetime import datetime, timezone
from typing import Any, Type

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from osint.errors import ScanStopped
from osint.state import ScanState
from osint.types import ToolCallRecord


class CappedTool(BaseTool):
    """Wraps a LangChain BaseTool: enforces per-scan caps and records every
    invocation to ScanState (including the raw vendor response via the
    `response_format="content_and_artifact"` convention).
    """

    name: str
    description: str
    args_schema: Type[BaseModel] | None = None
    response_format: str = "content"

    _wrapped: BaseTool = PrivateAttr()
    _state: ScanState = PrivateAttr()
    _est_cost_usd: float = PrivateAttr()
    _pending_tool_call_id: str | None = PrivateAttr(default=None)

    def __init__(self, wrapped: BaseTool, state: ScanState, est_cost_usd: float):
        super().__init__(
            name=wrapped.name,
            description=wrapped.description,
            args_schema=wrapped.args_schema,
            response_format=getattr(wrapped, "response_format", "content"),
        )
        self._wrapped = wrapped
        self._state = state
        self._est_cost_usd = est_cost_usd

    def _run(self, *args, **kwargs):
        raise NotImplementedError("CappedTool is async-only; use ainvoke().")

    async def ainvoke(
        self,
        input: str | dict | Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        # LangChain may receive `_tool_call_id` in the invocation dict when the
        # caller passes it (e.g. tests). Pop it before LangChain validates the
        # input against args_schema (which would otherwise drop it silently).
        if isinstance(input, dict) and "_tool_call_id" in input:
            input = dict(input)
            self._pending_tool_call_id = input.pop("_tool_call_id")
        else:
            self._pending_tool_call_id = None
        return await super().ainvoke(input, config=config, **kwargs)

    async def _arun(
        self,
        *args,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs,
    ):
        tool_call_id = getattr(self, "_pending_tool_call_id", None)

        stopped, reason = self._state.should_stop()
        if stopped:
            raise ScanStopped(reason.value)

        started = datetime.now(timezone.utc)
        content: Any = None
        artifact: Any = None
        error: str | None = None

        try:
            sig = inspect.signature(self._wrapped._arun)
            call_kwargs = dict(kwargs)
            if "run_manager" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                call_kwargs["run_manager"] = run_manager
            result = await self._wrapped._arun(*args, **call_kwargs)
            if self.response_format == "content_and_artifact" and isinstance(result, tuple):
                content, artifact = result
            else:
                content = result
                artifact = result
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            completed = datetime.now(timezone.utc)
            self._record(started, completed, tool_call_id, kwargs, None, None, error)
            raise

        completed = datetime.now(timezone.utc)
        output_dict = artifact if isinstance(artifact, dict) else {"text": str(content)}
        self._record(started, completed, tool_call_id, kwargs, output_dict, artifact, None)

        if self.response_format == "content_and_artifact":
            return content, artifact
        return content

    def _record(
        self,
        started: datetime,
        completed: datetime,
        tool_call_id: str | None,
        inputs: dict,
        output: dict | None,
        raw: Any,
        error: str | None,
    ) -> None:
        turn = len(self._state.tool_calls) + 1
        self._state.record_tool_call(ToolCallRecord(
            turn=turn,
            tool=self._wrapped.name,
            tool_call_id=tool_call_id,
            input=inputs,
            output=output,
            raw=raw,
            started_at=started,
            completed_at=completed,
            cost_usd=self._est_cost_usd,
            error=error,
        ))
