"""Test doubles shared by the Guardian tests.

`ScriptedChatModel` replays a fixed list of replies, so agent runs are
deterministic and use no API quota.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptExhaustedError(AssertionError):
    """The agent asked the model for more replies than the test scripted."""


class ScriptedChatModel(BaseChatModel):
    """A chat model that returns the next scripted message on each call.

    `calls` records the messages sent on every call and `bound_tool_names`
    the tools the agent offered, so tests can assert on what the model saw.
    """

    script: list[AIMessage]
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        self.bound_tool_names[:] = [_tool_name(tool) for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        index = len(self.calls)
        self.calls.append(list(messages))
        if index >= len(self.script):
            raise ScriptExhaustedError(
                f"model called {index + 1} times but only {len(self.script)} replies were scripted"
            )
        return ChatResult(generations=[ChatGeneration(message=self.script[index])])


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    """An assistant message that asks for one tool call."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def reply(text: str) -> AIMessage:
    """A plain assistant reply with no tool calls."""
    return AIMessage(content=text)


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(tool, dict):
        function = tool.get("function", {})
        return str(tool.get("name") or function.get("name") or "")
    return str(getattr(tool, "__name__", ""))
