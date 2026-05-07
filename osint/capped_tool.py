import contextvars
from datetime import datetime, timezone
from typing import Any, Type

import structlog
from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from osint.errors import ScanStopped
from osint.state import ScanState
from osint.types import ToolCallRecord

_tool_logger = structlog.get_logger("tool")


# Per-asyncio-task storage for the tool_call_id pulled from `ainvoke`'s input.
# Using a ContextVar (rather than an instance attribute) keeps concurrent
# ainvoke() tasks on the same shared CappedTool instance isolated from each
# other, which is the situation LangGraph's ToolNode produces when the agent
# emits multiple tool_calls in one turn.
_PENDING_TOOL_CALL_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_pending_tool_call_id", default=None
)


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
        # Pull `_tool_call_id` out of the input dict before LangChain's
        # args_schema parsing strips it as an unknown key. Stash on a
        # per-task ContextVar so concurrent ainvoke() calls don't race.
        if isinstance(input, dict) and "_tool_call_id" in input:
            input = dict(input)
            _PENDING_TOOL_CALL_ID.set(input.pop("_tool_call_id"))
        else:
            _PENDING_TOOL_CALL_ID.set(None)
        return await super().ainvoke(input, config=config, **kwargs)

    async def _arun(
        self,
        *args,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs,
    ):
        tool_call_id = _PENDING_TOOL_CALL_ID.get()

        stopped, reason = self._state.should_stop()
        if stopped:
            raise ScanStopped(reason.value)

        started = datetime.now(timezone.utc)
        content: Any = None
        artifact: Any = None
        error: str | None = None

        _tool_logger.info(
            "tool.started",
            scan_id=self._state.scan_id,
            tool_name=self._wrapped.name,
            args=kwargs,
        )

        try:
            result = await self._wrapped._arun(*args, run_manager=run_manager, **kwargs)
            if self.response_format == "content_and_artifact" and isinstance(result, tuple):
                content, artifact = result
            else:
                content = result
                artifact = result
        except ScanStopped:
            # Cap-cut from a tool we wrapped — propagate so scan() can run
            # synthesis. (CappedTool itself raises this above; this branch
            # is for the unlikely case where an inner tool nests a cap.)
            raise
        except Exception as e:
            # Convert inner-tool errors into a tool-message string the LLM
            # can react to, INSTEAD of re-raising. LangGraph's default
            # behaviour is to re-raise tool exceptions, which would crash
            # the entire scan on a single bad URL / blocked origin / 429.
            # By returning an error-content here, the agent sees the failure
            # in its conversation history and can adjust strategy
            # (try a different URL, switch tools, give up that thread).
            error = f"{type(e).__name__}: {e}"
            completed = datetime.now(timezone.utc)
            self._record(started, completed, tool_call_id, kwargs, None, None, error)
            _tool_logger.info(
                "tool.finished",
                scan_id=self._state.scan_id,
                tool_name=self._wrapped.name,
                args=kwargs,
                error=error,
            )
            error_content = f"Tool error from {self._wrapped.name}: {error}"
            if self.response_format == "content_and_artifact":
                return error_content, {"error": error}
            return error_content

        completed = datetime.now(timezone.utc)
        output_dict = artifact if isinstance(artifact, dict) else {"text": str(content)}
        self._record(started, completed, tool_call_id, kwargs, output_dict, artifact, None)

        # Best-effort size/count for the UI tail.
        result_count: int | None = None
        result_size_bytes: int | None = None
        if isinstance(artifact, list):
            result_count = len(artifact)
        elif isinstance(artifact, dict):
            for k in ("results", "items", "hits", "matches"):
                v = artifact.get(k)
                if isinstance(v, list):
                    result_count = len(v)
                    break
        try:
            result_size_bytes = len(str(content).encode("utf-8"))
        except Exception:
            pass

        _tool_logger.info(
            "tool.finished",
            scan_id=self._state.scan_id,
            tool_name=self._wrapped.name,
            args=kwargs,
            result_count=result_count,
            result_size_bytes=result_size_bytes,
        )

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
