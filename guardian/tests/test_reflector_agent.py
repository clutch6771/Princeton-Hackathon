"""Tests for the Reflector agent: which tools it has, what it may write, and its tools.

The first group runs before the Reflector is given any reel data: the default
`task` and `delete` tools must be gone and the planning tool present.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from guardian import middleware, reflector
from guardian.deps import Deps, ReflectorRun
from guardian.memory import (
    inbox,
    keys,
    notes,
    proposals,
    records,
    reflector_files,
    values,
)
from guardian.tests.conftest import NOW, OTHER_USER, USER
from guardian.tests.fakes import ScriptedChatModel, reply, tool_call

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
SESSION = "s_001"
RUN = ReflectorRun(user_id=USER, session_id=SESSION, run_id="run_1", since=EPOCH)
OPEN, CLOSE = middleware.UNTRUSTED_OPEN, middleware.UNTRUSTED_CLOSE

HUSTLE_CHANGE = {
    "value_id": "new",
    "new_text": """## New: Less get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, get-rich-quick pitches
- Unless: none
- Suggested wording: "I don't want hustle and get-rich-quick videos"
- Evidence: fb_002, fb_004, fb_007""",
    "evidence": "fb_002, fb_004, fb_007",
}
HUSTLE_PROMPT = "You marked 3 reels like this as not for you. I've suggested wording; edit it to make it yours."


def run_reflector(
    store, datasource, clock, script: list[AIMessage], run: ReflectorRun = RUN
):
    """Run the Reflector with scripted tool calls; return what the model saw and offered."""
    model = ScriptedChatModel(script=[*script, reply("done")])
    deps = Deps(
        store=store, checkpointer=InMemorySaver(), datasource=datasource, clock=clock
    )
    agent = reflector.build_reflector(deps, model, run)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Review the session."}]}
    )
    return model, [
        str(m.content) for m in result["messages"] if isinstance(m, ToolMessage)
    ]


def write(path: str, content: str, call: str = "c1") -> AIMessage:
    return tool_call("write_file", {"file_path": path, "content": content}, call)


def queue(
    change: dict[str, Any] = HUSTLE_CHANGE,
    call: str = "q1",
    prompt: str = HUSTLE_PROMPT,
) -> AIMessage:
    args = {"kind": "value_change", "prompt": prompt, "value_change": change}
    return tool_call("queue_question", args, call)


# --- Before any reel data: the tool set -----------------------------------------------


def test_reflector_has_no_task_or_delete_tool_and_has_the_planning_tool(
    store, datasource, clock
):
    model, _ = run_reflector(store, datasource, clock, [])

    offered = set(model.bound_tool_names)
    assert "task" not in offered and "delete" not in offered
    assert "write_todos" in offered
    assert (
        offered
        == reflector.ALLOWED_TOOLS
        == {
            "write_todos",
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "dump_session",
            "dump_feedback",
            "dump_chatlog",
            "feed_profile",
            "queue_question",
        }
    )


def test_calls_to_removed_tools_are_refused_and_do_nothing(store, datasource, clock):
    reflector_files.files_backend(store, USER).write("/brief.md", "Likes cooking.")
    script = [
        tool_call("delete", {"file_path": "/memories/brief.md"}, "c1"),
        tool_call(
            "task",
            {"description": "review everything", "subagent_type": "general-purpose"},
            "c2",
        ),
    ]

    _, results = run_reflector(store, datasource, clock, script)

    assert all("not available" in result for result in results)
    assert (
        reflector_files.read_file(store, USER, reflector_files.BRIEF)
        == "Likes cooking."
    )


def test_planning_tool_works(store, datasource, clock):
    todos = [{"content": "Dump the session", "status": "in_progress"}]

    _, results = run_reflector(
        store, datasource, clock, [tool_call("write_todos", {"todos": todos})]
    )

    assert len(results) == 1 and "not available" not in results[0]


def test_skill_and_own_files_are_loaded_into_the_prompt(store, datasource, clock):
    reflector_files.files_backend(store, USER).write(
        "/brief.md", "Unwinds with Reels at night."
    )

    model, _ = run_reflector(store, datasource, clock, [])

    prompt = " ".join(str(m.content) for m in model.calls[0][:1])
    assert "session-review" in prompt
    assert "Unwinds with Reels at night." in prompt
    assert "You never change the user's values" in prompt


# --- What the Reflector's file tools may write -----------------------------------------


def test_reflector_can_write_only_its_three_kinds_of_file(store, datasource, clock):
    script = [
        write("/memories/brief.md", "Likes cooking.", "c1"),
        write(
            "/memories/hypotheses.md",
            "## H1: Late nights drift\n- Evidence: s_001",
            "c2",
        ),
        write(f"/memories/sessions/{SESSION}.md", "A short summary.", "c3"),
        write("/memories/values.md", "## V1: mine (strength: high)", "c4"),
        write("/memories/notes.md", "- N1: invented", "c5"),
        write("/elsewhere/file.md", "stray", "c6"),
    ]

    _, results = run_reflector(store, datasource, clock, script)

    stored = {item.key for item in store.search(keys.reflector_files_namespace(USER))}
    assert stored == {"/brief.md", "/hypotheses.md", f"/sessions/{SESSION}.md"}
    assert all(
        "error" in r.lower() or "not" in r.lower() or "denied" in r.lower()
        for r in results[3:]
    )


def test_code_owned_records_are_out_of_reach_of_the_file_tools(
    store, datasource, clock, with_values
):
    records.append_chat_turn(store, USER, records.ROLE_USER, "a private message", NOW)
    script = [
        tool_call("read_file", {"file_path": "/memories/values.md"}, "c1"),
        tool_call("glob", {"pattern": "**/*"}, "c2"),
        tool_call("grep", {"pattern": "V1|private"}, "c3"),
        write("/memories/values.md", "## V1: replaced (strength: high)", "c4"),
    ]

    _, results = run_reflector(store, datasource, clock, script)

    assert not any(
        "Fitness without body comparison" in r or "a private message" in r
        for r in results
    )
    assert values.get_value(store, USER, "V1").text.startswith(
        "## V1: Fitness without body comparison"
    )
    assert len(values.list_values(store, USER)) == 3


@pytest.mark.parametrize(
    ("path", "content", "limit"),
    [
        ("/memories/brief.md", "word " * 151, "150"),
        (
            "/memories/hypotheses.md",
            "\n".join(f"## H{n}: pattern\n- Evidence: s_001" for n in range(1, 12)),
            "10",
        ),
        (f"/memories/sessions/{SESSION}.md", "word " * 201, "200"),
    ],
)
def test_over_cap_writes_are_refused_with_a_request_to_consolidate(
    store, datasource, clock, path, content, limit
):
    _, results = run_reflector(store, datasource, clock, [write(path, content)])

    assert limit in results[0] and "onsolidate" in results[0]
    assert store.search(keys.reflector_files_namespace(USER)) == []


def test_an_edit_that_would_exceed_a_cap_is_refused(store, datasource, clock):
    reflector_files.files_backend(store, USER).write("/brief.md", "Likes cooking.")
    edit = {
        "file_path": "/memories/brief.md",
        "old_string": "cooking",
        "new_string": "word " * 160,
    }

    _, results = run_reflector(store, datasource, clock, [tool_call("edit_file", edit)])

    assert "150" in results[0]
    assert (
        reflector_files.read_file(store, USER, reflector_files.BRIEF)
        == "Likes cooking."
    )


def test_a_valid_edit_is_applied(store, datasource, clock):
    reflector_files.files_backend(store, USER).write("/brief.md", "Likes cooking.")
    edit = {
        "file_path": "/memories/brief.md",
        "old_string": "cooking",
        "new_string": "budget cooking",
    }

    run_reflector(store, datasource, clock, [tool_call("edit_file", edit)])

    assert (
        reflector_files.read_file(store, USER, reflector_files.BRIEF)
        == "Likes budget cooking."
    )


def test_a_run_is_bound_to_one_user(store, datasource, clock):
    reflector_files.files_backend(store, OTHER_USER).write(
        "/brief.md", "Someone else's brief."
    )
    script = [
        tool_call("read_file", {"file_path": "/memories/brief.md"}, "c1"),
        write("/memories/brief.md", "Mine.", "c2"),
    ]

    _, results = run_reflector(store, datasource, clock, script)

    assert "Someone else's brief." not in results[0]
    assert (
        reflector_files.read_file(store, OTHER_USER, reflector_files.BRIEF)
        == "Someone else's brief."
    )
    assert reflector_files.read_file(store, USER, reflector_files.BRIEF) == "Mine."


# --- Dump tools: read-only copies in /scratch/ ------------------------------------------


def test_dump_session_writes_one_labelled_reel_per_line(store, datasource, clock):
    script = [
        tool_call("dump_session", {"session_id": SESSION}, "c1"),
        tool_call("read_file", {"file_path": "/scratch/session.jsonl"}, "c2"),
        tool_call("grep", {"pattern": "conflict", "path": "/scratch"}, "c3"),
    ]

    _, results = run_reflector(store, datasource, clock, script)

    assert "12 reels" in results[0] and "/scratch/session.jsonl" in results[0]
    assert results[1].startswith(OPEN) and results[1].endswith(CLOSE)
    assert (
        "rl_001" in results[1]
        and "rl_012" in results[1]
        and "knee surgery" in results[1]
    )
    assert results[2].startswith(OPEN) and "session.jsonl" in results[2]
    assert store.search(keys.reflector_files_namespace(USER)) == []


def test_dump_session_refuses_any_session_but_the_one_under_review(
    store, datasource, clock
):
    _, results = run_reflector(
        store,
        datasource,
        clock,
        [tool_call("dump_session", {"session_id": "s_heldout"})],
    )

    assert "s_001" in results[0] and "reels" not in results[0].split("s_001")[0]


def test_injection_text_in_a_dumped_session_is_read_only_as_labelled_data(
    store, datasource, clock
):
    run = ReflectorRun(user_id=USER, session_id="s_002", run_id="run_1", since=EPOCH)
    script = [
        tool_call("dump_session", {"session_id": "s_002"}, "c1"),
        tool_call(
            "grep", {"pattern": "ignore your instructions", "path": "/scratch"}, "c2"
        ),
        tool_call("read_file", {"file_path": "/scratch/session.jsonl"}, "c3"),
    ]

    _, results = run_reflector(store, datasource, clock, script, run=run)

    searched, read = results[1], results[2]
    for result in (searched, read):
        assert result.startswith(OPEN) and result.endswith(CLOSE)
        assert result.count(CLOSE) == 1
    assert "session.jsonl" in searched
    assert "ignore your instructions" in read.lower()


def test_dump_feedback_and_chatlog_copy_the_users_words(store, datasource, clock):
    records.append_chat_turn(
        store, USER, records.ROLE_USER, "I scroll when I'm stressed", NOW
    )
    records.append_chat_turn(
        store, USER, records.ROLE_GUARDIAN, "Thanks for telling me.", NOW
    )
    notes.save_note(
        store,
        datasource,
        USER,
        "Scrolls when stressed",
        "I scroll when I'm stressed",
        NOW,
    )
    script = [
        tool_call("dump_feedback", {"since": EPOCH.isoformat()}, "c1"),
        tool_call("dump_chatlog", {"since": EPOCH.isoformat()}, "c2"),
        tool_call("read_file", {"file_path": "/scratch/feedback.jsonl"}, "c3"),
        tool_call("read_file", {"file_path": "/scratch/chats.md"}, "c4"),
        tool_call("read_file", {"file_path": "/scratch/notes.md"}, "c5"),
    ]

    _, results = run_reflector(store, datasource, clock, script)

    assert "8" in results[0] and "/scratch/feedback.jsonl" in results[0]
    assert "/scratch/chats.md" in results[1] and "/scratch/notes.md" in results[1]
    assert (
        "fb_001" in results[2]
        and "not_for_me" in results[2]
        and "rehab not comparison" in results[2]
    )
    assert (
        "I scroll when I'm stressed" in results[3]
        and "Thanks for telling me." not in results[3]
    )
    assert "N1: Scrolls when stressed" in results[4]


def test_feed_profile_reports_the_session_under_review(store, datasource, clock):
    _, results = run_reflector(
        store, datasource, clock, [tool_call("feed_profile", {"session_id": SESSION})]
    )

    assert "Reels: 12" in results[0] and "Conflicts: 5" in results[0]


# --- queue_question: staging cards -------------------------------------------------------


def test_queue_question_stages_a_value_change_that_is_not_yet_in_the_inbox(
    store, datasource, clock
):
    _, results = run_reflector(store, datasource, clock, [queue()])

    assert "Queued" in results[0]
    staged = inbox.list_cards(store, USER, status=inbox.STATUS_STAGED)
    assert [(card.kind, card.prompt, card.run_id) for card in staged] == [
        ("value_change", HUSTLE_PROMPT, "run_1")
    ]
    assert inbox.list_cards(store, USER, status=inbox.STATUS_PENDING) == []
    proposal = proposals.get_proposal(store, USER, staged[0].proposal_id)
    assert proposal.status == proposals.STATUS_STAGED and proposal.target == "new"
    assert values.render_values(store, USER) == ""


def test_queue_question_refuses_a_quote_the_user_never_wrote(store, datasource, clock):
    change = {
        **HUSTLE_CHANGE,
        "new_text": HUSTLE_CHANGE["new_text"].replace(
            "Suggested wording", "In my words"
        ),
    }

    _, results = run_reflector(store, datasource, clock, [queue(change)])

    assert "Suggested wording" in results[0] and "Queued" not in results[0]
    assert inbox.list_cards(store, USER) == []


def test_queue_question_accepts_an_exact_quote_from_a_feedback_comment(
    store, datasource, clock, with_values
):
    stored = values.get_value(store, USER, "V1").text
    refined = stored.replace(
        "- Unless: none", "- Unless: injury rehab and recovery progress"
    ).replace(
        "gym stuff is great, comparing bodies is not",
        "rehab progress is fine, it's about getting better not looking better",
    )
    change = {
        "value_id": "V1",
        "new_text": refined,
        "evidence": "fb_001, fb_003, fb_005",
    }

    _, results = run_reflector(
        store,
        datasource,
        clock,
        [queue(change, prompt="You disagreed with 5 rehab flags.")],
    )

    assert "Queued" in results[0]


def test_queue_question_refuses_missing_evidence_and_bad_input(
    store, datasource, clock
):
    script = [
        queue({**HUSTLE_CHANGE, "evidence": "fb_999"}, "q1"),
        queue(None, "q2"),
        tool_call(
            "queue_question",
            {"kind": "value_change", "prompt": "  ", "value_change": HUSTLE_CHANGE},
            "q3",
        ),
        tool_call(
            "queue_question",
            {"kind": "rerate", "prompt": "Quick check", "reel_ids": ["rl_003"]},
            "q4",
        ),
    ]

    _, results = run_reflector(store, datasource, clock, script)

    assert "fb_999" in results[0]
    assert all("Queued" not in result for result in results)
    assert "not enabled" in results[3]
    assert inbox.list_cards(store, USER) == []


def test_queue_question_allows_two_value_changes_per_run(store, datasource, clock):
    def variant(number: int) -> dict[str, Any]:
        text = HUSTLE_CHANGE["new_text"].replace(
            "Less get-rich-quick content", f"Topic {number}"
        )
        return {**HUSTLE_CHANGE, "new_text": text}

    script = [queue(variant(1), "q1"), queue(variant(2), "q2"), queue(variant(3), "q3")]

    _, results = run_reflector(store, datasource, clock, script)

    assert "Queued" in results[0] and "Queued" in results[1]
    assert "2" in results[2] and "Queued" not in results[2]
    assert len(inbox.list_cards(store, USER, status=inbox.STATUS_STAGED)) == 2


def test_queue_question_refuses_a_change_already_pending_staged_or_rejected(
    store, datasource, clock
):
    first = proposals.create_proposal(
        store,
        datasource,
        USER,
        source=proposals.reflector_source("s_000", "run_0"),
        now=NOW,
        **HUSTLE_CHANGE,
    )
    proposals.decide(
        store, USER, first.proposal.id, action="reject", edited_text=None, now=NOW
    )
    other = {
        **HUSTLE_CHANGE,
        "new_text": HUSTLE_CHANGE["new_text"].replace(
            "Less get-rich", "Fewer get-rich"
        ),
    }

    _, results = run_reflector(
        store,
        datasource,
        clock,
        [queue(HUSTLE_CHANGE, "q1"), queue(other, "q2"), queue(other, "q3")],
    )

    assert "rejected" in results[0]
    assert "Queued" in results[1]
    assert "already" in results[2]


def test_queue_question_respects_the_five_pending_cards_cap(store, datasource, clock):
    for number in range(inbox.MAX_PENDING):
        inbox.add_card(
            store,
            inbox.new_card(
                USER, "learned", f"Earlier card {number}", NOW, card_id=f"old_{number}"
            ),
        )

    _, results = run_reflector(store, datasource, clock, [queue()])

    assert inbox.MAX_PENDING == 5
    assert "5" in results[0] and "Queued" not in results[0]


def test_dumped_files_are_valid_json_lines(store, datasource, clock):
    script = [
        tool_call("dump_session", {"session_id": SESSION}, "c1"),
        tool_call(
            "read_file", {"file_path": "/scratch/session.jsonl", "limit": 2}, "c2"
        ),
    ]

    _, results = run_reflector(store, datasource, clock, script)

    body = results[1][len(OPEN) : -len(CLOSE)]
    first_json = next(
        line[line.index("{") :] for line in body.splitlines() if "{" in line
    )
    record = json.loads(first_json)
    assert record["id"] == "rl_001" and len(record["transcript"]) <= 401
    assert set(record) >= {
        "id",
        "description",
        "transcript",
        "features",
        "verdict",
        "confidence",
        "value_ids",
    }
