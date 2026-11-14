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
