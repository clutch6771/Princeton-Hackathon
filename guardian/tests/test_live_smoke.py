"""Live smoke tests against the real model API. They use quota: run sparingly.

Run with:  python -m pytest guardian/tests -m live
Choose the model with GUARDIAN_CHAT_MODEL (default: guardian.config.DEFAULT_CHAT_MODEL).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from guardian import config

pytestmark = pytest.mark.live

THREAD = {"configurable": {"thread_id": "u1:smoke"}}
REQUEST_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class Ctx:
    user_id: str


@pytest.fixture(scope="module")
def model() -> Any:
    config.load_env()
    if not config.has_api_key():
        pytest.skip(f"{config.API_KEY_VAR} is not set")
    return init_chat_model(
        config.chat_model_name(),
        temperature=0,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=2,
    )


def user_says(text: str) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": text}]}


def final_text(result: dict[str, Any]) -> str:
    content = result["messages"][-1].content
    if isinstance(content, str):
        return content
    return " ".join(
        str(block.get("text", "")) for block in content if isinstance(block, dict)
    )


def test_model_completes_a_one_tool_call(model: Any):
    @tool
    def count_reels(session_id: str) -> str:
        """Return how many reels the user watched in a session. Call this before answering."""
        return f"Session {session_id} has 7 reels."

    agent = create_agent(model=model, tools=[count_reels])

    result = agent.invoke(
        user_says("How many reels were in session s_001? Use the tool.")
    )

    tool_results = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_results) >= 1
    assert "7" in final_text(result)


def test_model_pauses_on_an_in_tool_interrupt_and_continues_after_resume(model: Any):
    executions: list[str] = []

    @tool
    def propose_value(text: str, runtime: ToolRuntime[Ctx]) -> str:
        """Propose a value for the user to approve. The user sees a card and decides."""
        executions.append(runtime.tool_call_id)
        decision = interrupt({"proposal": text})
        return f"The user's decision: {decision['outcome']}."

    agent = create_agent(
        model=model,
        tools=[propose_value],
        context_schema=Ctx,
        checkpointer=InMemorySaver(),
    )
    request = "I want to see less outrage politics. Propose that as a value now with the propose_value tool."

    paused = agent.invoke(user_says(request), config=THREAD, context=Ctx("u1"))
    resumed = agent.invoke(
        Command(resume={"outcome": "approved"}), config=THREAD, context=Ctx("u1")
    )

    assert len(paused["__interrupt__"]) == 1
    assert "proposal" in paused["__interrupt__"][0].value
    assert len(executions) == 2 and executions[0] == executions[1]
    assert "__interrupt__" not in resumed
    assert final_text(resumed).strip() != ""
