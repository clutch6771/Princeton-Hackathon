"""Pins the LangChain and Deep Agents behaviour the Guardian design depends on.

These are characterization tests of the installed libraries, written in Step 0.
If a dependency upgrade changes any of this behaviour, a test here fails before
the Guardian or Reflector misbehaves. Each test names the design decision that
relies on it. No test calls a real model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol, WriteResult
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentState,
    ModelCallLimitMiddleware,
    ModelRequest,
    TodoListMiddleware,
    after_model,
    dynamic_prompt,
    wrap_tool_call,
)
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt

from guardian.tests.fakes import ScriptedChatModel, reply, tool_call

USER_FILES = ("u1", "reflector_files")
THREAD = {"configurable": {"thread_id": "u1:2026-11-14"}}
WORD_CAP = 5


@dataclass(frozen=True)
class Ctx:
    user_id: str


def user_says(text: str) -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": text}]}


def text_of(message: Any) -> str:
    """Flatten a message's content, which may be a string or content blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    return " ".join(str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in content)


def system_text(model: ScriptedChatModel, call: int = 0) -> str:
    return " ".join(text_of(m) for m in model.calls[call] if isinstance(m, SystemMessage))


def tool_messages(result: dict[str, Any]) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


# --- Chat agent: approval by an interrupt raised inside the tool -------------------


def build_proposing_agent(script: list[AIMessage], log: list[dict[str, str]]):
    @tool
    def propose(text: str, runtime: ToolRuntime[Ctx]) -> str:
        """Propose a change and wait for the user's decision."""
        log.append({"tool_call_id": runtime.tool_call_id, "user_id": runtime.context.user_id})
        answer = interrupt({"proposal": text})
        return f"outcome: {answer['outcome']}"

    model = ScriptedChatModel(script=script)
    agent = create_agent(model=model, tools=[propose], context_schema=Ctx, checkpointer=InMemorySaver())
    return agent, model, propose


def test_runtime_argument_is_hidden_from_the_tool_schema():
    # Design: tools take no user ID from the model; it comes from runtime context.
    _, _, propose = build_proposing_agent([reply("unused")], [])

    properties = propose.tool_call_schema.model_json_schema()["properties"]

    assert set(properties) == {"text"}


def test_tool_interrupt_pauses_run_and_surfaces_payload():
    # Design: propose_value_change runs its checks, then raises the interrupt itself.
    log: list[dict[str, str]] = []
    agent, model, _ = build_proposing_agent([tool_call("propose", {"text": "less outrage"})], log)

    result = agent.invoke(user_says("hi"), config=THREAD, context=Ctx("u1"))

    assert [i.value for i in result["__interrupt__"]] == [{"proposal": "less outrage"}]
    assert log == [{"tool_call_id": "call_1", "user_id": "u1"}]
    assert len(model.calls) == 1


def test_resume_reruns_tool_body_with_same_tool_call_id():
    # Design: the tool must find its existing proposal by tool-call ID on resume,
    # because everything before interrupt() runs a second time.
    log: list[dict[str, str]] = []
    script = [tool_call("propose", {"text": "less outrage"}), reply("Saved.")]
    agent, model, _ = build_proposing_agent(script, log)
    agent.invoke(user_says("hi"), config=THREAD, context=Ctx("u1"))

    result = agent.invoke(Command(resume={"outcome": "approved"}), config=THREAD, context=Ctx("u1"))

    assert log == [{"tool_call_id": "call_1", "user_id": "u1"}] * 2
    assert tool_messages(result)[-1].content == "outcome: approved"
    assert text_of(result["messages"][-1]) == "Saved."
    assert "__interrupt__" not in result


def test_paused_thread_reports_pending_interrupt_in_state():
    # Design: resume() can tell whether a thread is actually paused.
    agent, _, _ = build_proposing_agent([tool_call("propose", {"text": "x"}), reply("ok")], [])
    agent.invoke(user_says("hi"), config=THREAD, context=Ctx("u1"))

    paused = agent.get_state(THREAD)
    agent.invoke(Command(resume={"outcome": "rejected"}), config=THREAD, context=Ctx("u1"))
    finished = agent.get_state(THREAD)

    assert len(paused.interrupts) == 1
    assert paused.next != ()
    assert finished.interrupts == ()
    assert finished.next == ()


# --- Chat agent: middleware ---------------------------------------------------------


def test_dynamic_prompt_reads_runtime_context():
    # Design: values and the brief are inserted into the system prompt on every call.
    @dynamic_prompt
    def prompt(request: ModelRequest) -> str:
        return f"Values for {request.runtime.context.user_id}: V1"

    model = ScriptedChatModel(script=[reply("hello")])
    agent = create_agent(model=model, tools=[], middleware=[prompt], context_schema=Ctx)

    agent.invoke(user_says("hi"), context=Ctx("u7"))

    assert "Values for u7: V1" in system_text(model)


def test_after_model_can_jump_back_to_model_once():
    # Design: the value-citation check retries once when a reply cites an unknown ID.
    @after_model(can_jump_to=["model"])
    def citation_check(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state["messages"]
        corrected = any(isinstance(m, HumanMessage) and "CORRECTION" in text_of(m) for m in messages)
        if "V99" in text_of(messages[-1]) and not corrected:
            return {"messages": [HumanMessage(content="CORRECTION: V99 is not stored")], "jump_to": "model"}
        return None

    model = ScriptedChatModel(script=[reply("That touches V99."), reply("No stored value matches.")])
    agent = create_agent(model=model, tools=[], middleware=[citation_check])

    result = agent.invoke(user_says("why?"))

    assert len(model.calls) == 2
    assert text_of(result["messages"][-1]) == "No stored value matches."


def test_after_model_can_replace_reply_by_message_id():
    # Design: after the one retry, the check replaces the reply with a plain message.
    @after_model
    def replace_bad_reply(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        last = state["messages"][-1]
        if "V99" in text_of(last):
            return {"messages": [AIMessage(content="No stored value matches.", id=last.id)]}
        return None

    model = ScriptedChatModel(script=[reply("That touches V99.")])
    agent = create_agent(model=model, tools=[], middleware=[replace_bad_reply])

    result = agent.invoke(user_says("why?"))

    ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
    assert [text_of(m) for m in ai_messages] == ["No stored value matches."]


def looping_script(count: int) -> list[AIMessage]:
    return [tool_call("noop", {}, call_id=f"call_{i}") for i in range(count)]


@tool
def noop() -> str:
    """Do nothing."""
    return "ok"


def test_model_call_limit_ends_run_after_limit():
    # Design: chat is capped at 6 model calls per message.
    model = ScriptedChatModel(script=looping_script(5))
    limit = ModelCallLimitMiddleware(run_limit=2, exit_behavior="end")
    agent = create_agent(model=model, tools=[noop], middleware=[limit])

    result = agent.invoke(user_says("go"))

    assert len(model.calls) == 2
    assert isinstance(result["messages"][-1], AIMessage)


def test_model_call_limit_error_mode_raises():
    # Design: the Reflector's 25-call limit ends the run as failed, so state is restored.
    model = ScriptedChatModel(script=looping_script(5))
    limit = ModelCallLimitMiddleware(run_limit=2, exit_behavior="error")
    agent = create_agent(model=model, tools=[noop], middleware=[limit])

    with pytest.raises(ModelCallLimitExceededError):
        agent.invoke(user_says("go"))

    assert len(model.calls) == 2


def test_wrap_tool_call_can_rewrite_tool_result():
    # Design: the frame-text guard labels reel text before the model reads it.
    @wrap_tool_call
    def guard(request: Any, handler: Any) -> Any:
        result = handler(request)
        if isinstance(result, ToolMessage):
            return result.model_copy(update={"content": f"<untrusted>{result.content}</untrusted>"})
        return result

    model = ScriptedChatModel(script=[tool_call("noop", {}), reply("done")])
    agent = create_agent(model=model, tools=[noop], middleware=[guard])

    agent.invoke(user_says("go"))

    seen_by_model = [m for m in model.calls[1] if isinstance(m, ToolMessage)]
    assert [m.content for m in seen_by_model] == ["<untrusted>ok</untrusted>"]


# --- Reflector: files, permissions, skills ------------------------------------------


class WordCapBackend(BackendProtocol):
    """Policy wrapper: rejects writes over a word cap, delegates everything else."""

    def __init__(self, inner: BackendProtocol) -> None:
        self._inner = inner

    def write(self, file_path: str, content: str) -> WriteResult:
        if len(content.split()) > WORD_CAP:
            return WriteResult(error=f"{file_path} is over its {WORD_CAP}-word cap. Consolidate first.")
        return self._inner.write(file_path, content)

    def ls(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.ls(*args, **kwargs)

    def read(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.read(*args, **kwargs)

    def edit(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.edit(*args, **kwargs)

    def glob(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.glob(*args, **kwargs)

    def grep(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.grep(*args, **kwargs)


def user_files(store: InMemoryStore) -> StoreBackend:
    return StoreBackend(namespace=lambda _rt: USER_FILES, store=store)


def build_reflector_like_agent(script: list[AIMessage], backend: BackendProtocol, store: InMemoryStore, **options: Any):
    model = ScriptedChatModel(script=script)
    agent = create_deep_agent(
        model=model, backend=backend, store=store, checkpointer=InMemorySaver(), context_schema=Ctx, **options
    )
    return agent, model


def run(agent: Any) -> dict[str, Any]:
    return agent.invoke(user_says("review session s_001"), config=THREAD, context=Ctx("u1"))


def test_deep_agent_file_write_lands_in_the_users_store_namespace():
    # Design: Reflector-owned files persist per user; the route prefix is stripped from the key.
    store = InMemoryStore()
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": user_files(store)})
    script = [tool_call("write_file", {"file_path": "/memories/brief.md", "content": "Likes cooking."}), reply("done")]
    agent, _ = build_reflector_like_agent(script, backend, store)

    run(agent)

    item = store.get(USER_FILES, "/brief.md")
    assert item is not None
    assert item.value["content"] == "Likes cooking."
    assert item.value["encoding"] == "utf-8"


def test_store_backed_file_is_readable_and_writable_from_code_outside_a_run():
    # Design: package code reads brief.md for the chat prompt and snapshots files before a run.
    store = InMemoryStore()
    files = user_files(store)

    written = files.write("/brief.md", "Prefers short replies.")
    read_back = files.read("/brief.md")

    assert written.error is None
    assert read_back.error is None
    assert read_back.file_data["content"] == "Prefers short replies."


def test_write_file_overwrites_an_existing_store_file():
    # Design: the Reflector rewrites brief.md on every run.
    store = InMemoryStore()
    files = user_files(store)
    files.write("/brief.md", "old")
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": files})
    script = [tool_call("write_file", {"file_path": "/memories/brief.md", "content": "new"}), reply("done")]
    agent, _ = build_reflector_like_agent(script, backend, store)

    result = run(agent)

    assert store.get(USER_FILES, "/brief.md").value["content"] == "new", tool_messages(result)[0].content


def test_permissions_deny_writes_outside_the_allowed_paths():
    # Design: second barrier. Values are not mounted at all; this stops stray files too.
    store = InMemoryStore()
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": user_files(store)})
    allowed = ["/memories/brief.md", "/memories/hypotheses.md", "/memories/sessions/**", "/scratch/**"]
    permissions = [
        FilesystemPermission(operations=["write"], paths=allowed, mode="allow"),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ]
    script = [
        tool_call("write_file", {"file_path": "/memories/values.md", "content": "## V1: mine"}, "call_1"),
        tool_call("write_file", {"file_path": "/memories/brief.md", "content": "ok"}, "call_2"),
        tool_call("write_file", {"file_path": "/memories/sessions/s_001.md", "content": "summary"}, "call_3"),
        reply("done"),
    ]
    agent, _ = build_reflector_like_agent(script, backend, store, permissions=permissions)

    result = run(agent)

    assert store.get(USER_FILES, "/values.md") is None
    assert store.get(USER_FILES, "/brief.md") is not None
    assert store.get(USER_FILES, "/sessions/s_001.md") is not None
    assert "permission" in tool_messages(result)[0].content.lower()


def test_custom_tool_can_write_a_scratch_file_the_agent_then_reads():
    # Design: dump_session writes /scratch/session.jsonl for the agent to grep.
    store = InMemoryStore()
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": user_files(store)})

    @tool
    def dump_session(session_id: str) -> str:
        """Write the session's reels to /scratch/session.jsonl."""
        outcome = backend.write("/scratch/session.jsonl", '{"id": "rl_001", "verdict": "conflict"}\n')
        return f"wrote /scratch/session.jsonl (error={outcome.error})"

    script = [
        tool_call("dump_session", {"session_id": "s_001"}, "call_1"),
        tool_call("grep", {"pattern": "conflict", "path": "/scratch"}, "call_2"),
        tool_call("read_file", {"file_path": "/scratch/session.jsonl"}, "call_3"),
        reply("done"),
    ]
    agent, _ = build_reflector_like_agent(script, backend, store, tools=[dump_session])

    result = run(agent)

    dumped, grepped, read = (m.content for m in tool_messages(result))
    assert "error=None" in dumped
    assert "session.jsonl" in grepped
    assert "rl_001" in read
    assert store.search(USER_FILES) == []


def test_policy_wrapper_rejects_a_write_and_the_error_reaches_the_model():
    # Design: caps on brief.md, hypotheses.md and session summaries are enforced in code.
    store = InMemoryStore()
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": WordCapBackend(user_files(store))})
    long_text = "one two three four five six seven"
    script = [
        tool_call("write_file", {"file_path": "/memories/brief.md", "content": long_text}, "call_1"),
        tool_call("write_file", {"file_path": "/memories/brief.md", "content": "short enough"}, "call_2"),
        reply("done"),
    ]
    agent, _ = build_reflector_like_agent(script, backend, store)

    result = run(agent)

    assert "Consolidate first" in tool_messages(result)[0].content
    assert store.get(USER_FILES, "/brief.md").value["content"] == "short enough"


def write_skill(skills_dir: Path) -> None:
    skill_dir = skills_dir / "session-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: session-review\n"
        "description: Use at the start of every run to review one finished scrolling session.\n"
        "---\n# Review a session\n\n1. Plan with write_todos.\n"
    )


def test_skills_load_from_a_local_folder_through_a_filesystem_route(tmp_path: Path):
    # Design: skills live in guardian/skills/ on disk, mounted read-only at /skills/.
    write_skill(tmp_path / "skills")
    store = InMemoryStore()
    routes = {
        "/memories/": user_files(store),
        "/skills/": FilesystemBackend(root_dir=tmp_path / "skills", virtual_mode=True),
    }
    backend = CompositeBackend(default=StateBackend(), routes=routes)
    script = [tool_call("read_file", {"file_path": "/skills/session-review/SKILL.md"}), reply("done")]
    agent, model = build_reflector_like_agent(script, backend, store, skills=["/skills/"])

    result = run(agent)

    assert "session-review" in system_text(model)
    assert "review one finished scrolling session" in system_text(model)
    assert "Plan with write_todos" in tool_messages(result)[0].content


def test_memory_files_are_preloaded_into_the_prompt():
    # Design: the Reflector starts each run with brief.md and hypotheses.md in context.
    store = InMemoryStore()
    files = user_files(store)
    files.write("/brief.md", "Unwinds with Reels late at night.")
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": files})
    agent, model = build_reflector_like_agent(
        [reply("done")], backend, store, memory=["/memories/brief.md", "/memories/hypotheses.md"]
    )

    run(agent)

    assert "Unwinds with Reels late at night." in system_text(model)


def test_deep_agent_default_tools_have_no_planning_tool():
    # Finding: deepagents 0.7.38 offers file tools plus `task` and `delete`, but not
    # write_todos. The Reflector must add planning itself and drop `task` and `delete`.
    store = InMemoryStore()
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": user_files(store)})
    agent, model = build_reflector_like_agent([reply("done")], backend, store)

    run(agent)

    assert set(model.bound_tool_names) == {
        "ls", "read_file", "write_file", "edit_file", "glob", "grep", "delete", "task",
    }


def test_todo_list_middleware_adds_the_planning_tool():
    # Design: the Reflector plans with write_todos, supplied by TodoListMiddleware.
    store = InMemoryStore()
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": user_files(store)})
    agent, model = build_reflector_like_agent([reply("done")], backend, store, middleware=[TodoListMiddleware()])

    run(agent)

    assert "write_todos" in model.bound_tool_names


def test_deep_agent_accepts_call_limit_and_passes_context_to_tools():
    # Design: 25-call limit on the Reflector; its tools are bound to one user by context.
    store = InMemoryStore()
    backend = CompositeBackend(default=StateBackend(), routes={"/memories/": user_files(store)})
    seen_users: list[str] = []

    @tool
    def whoami(runtime: ToolRuntime[Ctx]) -> str:
        """Report the current user."""
        seen_users.append(runtime.context.user_id)
        return "ok"

    script = [tool_call("whoami", {}, call_id=f"call_{i}") for i in range(5)]
    limit = ModelCallLimitMiddleware(run_limit=2, exit_behavior="error")
    agent, model = build_reflector_like_agent(script, backend, store, tools=[whoami], middleware=[limit])

    with pytest.raises(ModelCallLimitExceededError):
        run(agent)

    assert len(model.calls) == 2
    assert seen_users == ["u1", "u1"]
