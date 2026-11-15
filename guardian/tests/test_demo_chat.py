"""Tests for the terminal chat script (python -m guardian.demo_chat).

The script is a thin shell over the entry points; these tests drive its command
handling with a scripted model.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from guardian import api, demo_chat
from guardian.tests.conftest import USER
from guardian.tests.fakes import reply, tool_call

SESSION = demo_chat.Session(user_id=USER, thread_id=f"{USER}:2026-11-14")
QUOTE = "gym stuff is great, comparing bodies is not"

NEW_FITNESS = f"""## New: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said in chat"""


def propose_fitness():
    args = {"value_id": "new", "new_text": NEW_FITNESS, "evidence": "said in chat"}
    return tool_call("propose_value_change", args)


def run(line: str, edited: str = "") -> str:
    return "\n".join(demo_chat.handle(SESSION, line, read_block=lambda: edited))


def test_a_message_prints_the_guardians_reply(start):
    start([reply("What would you like to see less of?")])

    assert run("hi") == "Guardian: What would you like to see less of?"


def test_a_proposal_prints_the_value_card_and_how_to_answer_it(start):
    start([propose_fitness()])

    output = run(f"Honestly, {QUOTE}.")

    assert "VALUE CARD (new value)" in output
    assert NEW_FITNESS in output
    assert "Evidence given: said in chat" in output
    assert "/approve" in output and "/edit" in output and "/reject" in output


def test_approve_stores_the_value_and_values_prints_it(start):
    start([propose_fitness(), reply("Saved.")])
    run(f"Honestly, {QUOTE}.")

    approved = run("/approve")
    listed = run("/values")

    assert approved == "Guardian: Saved."
    assert listed.startswith("## V1: Fitness without body comparison (strength: high)")


def test_reject_stores_nothing(start):
    start([propose_fitness(), reply("No problem.")])
    run(f"Honestly, {QUOTE}.")

    assert run("/reject") == "Guardian: No problem."
    assert run("/values") == demo_chat.NO_VALUES


def test_edit_reads_the_new_text_and_stores_it(start):
    start([propose_fitness(), reply("Updated.")])
    run(f"Honestly, {QUOTE}.")
    edited = NEW_FITNESS.replace("- Unless: none", "- Unless: physio and rehab")

    assert run("/edit", edited=edited) == "Guardian: Updated."
    assert "- Unless: physio and rehab" in run("/values")


def test_an_error_is_printed_and_the_card_is_shown_again(start):
    start([propose_fitness()])
    run(f"Honestly, {QUOTE}.")

    output = run("/edit", edited="not a value entry")

    assert output.startswith("! ")
    assert "VALUE CARD (new value)" in output


def test_answering_without_a_card_prints_the_error(start):
    start([reply("unused")])

    assert run("/approve").startswith("! ")


def test_drift_opens_an_explanation_of_the_demo_reels(start, with_values):
    model = start(
        [
            tool_call("get_reels", {"reel_ids": list(demo_chat.DRIFT_REELS)}),
            reply("The first touched V1."),
        ]
    )

    output = run("/drift")

    sent = [str(m.content) for m in model.calls[0] if isinstance(m, HumanMessage)][-1]
    assert "entry: drift_notification" in sent
    assert "reel_ids: rl_014, rl_015, rl_016" in sent
    assert output == "Guardian: The first touched V1."


def test_seed_installs_the_personas_values_once(start):
    start([reply("unused")])

    first = run("/seed")
    second = run("/seed")

    assert "V1, V2, V3" in first and "V1, V2, V3" in second
    assert api.get_values(USER).count("## V") == 3


def test_memory_prints_each_stored_file(start, with_values):
    start([reply("unused")])

    output = run("/memory")

    for name in ("values.md", "brief.md", "hypotheses.md", "notes.md"):
        assert name in output
    assert "## V1:" in output and demo_chat.EMPTY_FILE in output


def test_help_and_unknown_commands_list_what_is_available(start):
    start([reply("unused")])

    assert run("/help") == demo_chat.HELP
    assert run("/dance").startswith("! Unknown command")
    assert run("   ") == ""


# --- The inbox commands: /reflect, /inbox, /answer -------------------------------------

HUSTLE_CARD = """## New: Less get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, get-rich-quick pitches
- Unless: none
- Suggested wording: "I don't want hustle and get-rich-quick videos"
- Evidence: fb_002, fb_004, fb_007"""


def fill_inbox(store, datasource) -> None:
    """One "What I learned" card and one value-change card, as a review would leave them."""
    from guardian.memory import inbox, proposals
    from guardian.tests.conftest import NOW

    learned = inbox.new_card(
        USER,
        "learned",
        "- You skipped 3 hustle reels. [fb_002]\n\nWhat made them easy to skip?",
        NOW,
        card_id="learned_s_001",
        session_id="s_001",
    )
    inbox.add_card(store, learned)
    created = proposals.create_proposal(
        store,
        datasource,
        USER,
        value_id="new",
        new_text=HUSTLE_CARD,
        evidence="fb_002, fb_004, fb_007",
        source=proposals.reflector_source("s_001", "run_1"),
        now=NOW,
    )
    card = inbox.new_card(
        USER,
        "value_change",
        "You marked 3 reels as not for you.",
        NOW.replace(minute=1),
        session_id="s_001",
        proposal_id=created.proposal.id,
        run_id="run_1",
    )
    inbox.add_card(store, card)


def test_inbox_lists_numbered_cards_and_says_when_it_is_empty(start, store, datasource):
    start([reply("unused")])
    assert run("/inbox") == demo_chat.EMPTY_INBOX
    fill_inbox(store, datasource)

    output = run("/inbox")

    assert "[1] What I learned" in output and "What made them easy to skip?" in output
    assert "[2] VALUE CARD (new value)" in output and HUSTLE_CARD in output
    assert "You marked 3 reels as not for you." in output


def test_answer_applies_the_users_decision_to_a_numbered_card(start, store, datasource):
    start([reply("unused")])
    fill_inbox(store, datasource)

    approved = run("/answer 2 approve")
    dismissed = run("/answer 1 dismiss")

    assert approved == demo_chat.ANSWERED and dismissed == demo_chat.ANSWERED
    assert "## V1: Less get-rich-quick content" in run("/values")
    assert run("/inbox") == demo_chat.EMPTY_INBOX


def test_answer_edit_reads_the_new_text(start, store, datasource):
    start([reply("unused")])
    fill_inbox(store, datasource)
    edited = HUSTLE_CARD.replace(
        "I don't want hustle and get-rich-quick videos", "no money-bro stuff"
    )

    assert run("/answer 2 edit", edited=edited) == demo_chat.ANSWERED
    assert '- In my words: "no money-bro stuff"' in run("/values")


def test_answer_reports_errors_and_bad_usage(start, store, datasource):
    start([reply("unused")])
    fill_inbox(store, datasource)

    assert run("/answer 1 approve").startswith("! ")
    assert run("/answer 9 approve").startswith("! ")
    assert run("/answer two approve").startswith("! ")
    assert run("/answer 2").startswith("! ")
    assert "[2] VALUE CARD" in run("/inbox")


def test_reflect_reviews_finished_sessions_and_shows_the_inbox(
    store, datasource, clock
):
    from langgraph.checkpoint.memory import InMemorySaver

    from guardian.tests.fakes import ScriptedChatModel

    summary = (
        "# Session {s}\nA short session.\n\n## What I learned\n"
        "- Some reels conflicted with your values. [{s}]\n\n## A question for you\nWhat stood out?"
    )
    script = []
    for session in ("s_001", "s_002"):
        path = f"/memories/sessions/{session}.md"
        script += [
            tool_call(
                "write_file",
                {"file_path": path, "content": summary.format(s=session)},
                session,
            ),
            reply("Review complete."),
        ]
    api.configure(
        store,
        InMemorySaver(),
        datasource,
        clock=clock,
        reflector_model=ScriptedChatModel(script=script),
    )

    output = run("/reflect")

    assert output.startswith("Reviewed 2 sessions.")
    assert output.count("What I learned") == 2 and "What stood out?" in output
