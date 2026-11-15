"""Tests for the protected values store, proposals and the single write path."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from langgraph.store.memory import InMemoryStore

from guardian.datasource import FakeDataSource
from guardian.memory import checks, proposals, records, values
from guardian.tests.conftest import NOW, OTHER_USER, USER

THREAD = f"{USER}:2026-11-14"
QUOTE = "gym stuff is great, comparing bodies is not"

NEW_FITNESS = f"""## New: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said at onboarding"""

NEW_HUSTLE = """## New: Less get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, get-rich-quick pitches
- Unless: none
- Suggested wording: "I don't want hustle and get-rich-quick videos"
- Evidence: fb_002, fb_004, fb_007"""


def say(store: InMemoryStore, text: str, user: str = USER) -> None:
    records.append_chat_turn(store, user, "user", text, NOW)


def propose(
    store,
    datasource,
    text=NEW_FITNESS,
    value_id="new",
    evidence="said at onboarding",
    thread=THREAD,
    call="call_1",
    user=USER,
):
    return proposals.create_proposal(
        store,
        datasource,
        user,
        value_id=value_id,
        new_text=text,
        evidence=evidence,
        source=proposals.chat_source(thread, call),
        now=NOW,
    )


def decide(store, proposal_id, action="approve", edited_text=None, user=USER):
    return proposals.decide(
        store, user, proposal_id, action=action, edited_text=edited_text, now=NOW
    )


def approved_value(
    store, datasource, text=NEW_HUSTLE, call="call_seed", thread=THREAD
) -> str:
    created = propose(
        store, datasource, text=text, evidence="fb_002", call=call, thread=thread
    )
    assert created.error is None, created.error
    return decide(store, created.proposal.id).value_id


# --- Creating a proposal ----------------------------------------------------------


def test_valid_proposal_with_a_verified_quote_is_recorded_as_pending(store, datasource):
    say(store, f"Honestly, {QUOTE}.")

    created = propose(store, datasource)

    assert created.error is None
    proposal = created.proposal
    assert (
        proposal.status == "pending"
        and proposal.target == "new"
        and proposal.old_text is None
    )
    assert proposals.get_proposal(store, USER, proposal.id) == proposal
    assert proposal.to_value_change() == {
        "value_id": "new",
        "old_text": None,
        "new_text": NEW_FITNESS,
        "evidence": "said at onboarding",
    }


def test_quote_the_user_never_wrote_is_refused_and_nothing_is_recorded(
    store, datasource
):
    created = propose(store, datasource)

    assert created.proposal is None
    assert "Suggested wording" in created.error
    assert proposals.list_proposals(store, USER) == []


def test_quote_check_ignores_case_spacing_and_surrounding_punctuation(
    store, datasource
):
    say(store, "Gym  stuff is GREAT,\ncomparing bodies is not!!")

    assert propose(store, datasource).error is None


def test_a_comment_on_app_feedback_is_not_the_users_words(store, datasource):
    # The app sends preset correction strings as the comment, not typed text.
    text = NEW_FITNESS.replace(QUOTE, "physio before and after is different")

    created = propose(store, datasource, text=text)

    assert created.proposal is None
    assert "Suggested wording" in created.error


def test_quote_can_come_from_a_comment_the_user_typed_outside_the_app_buttons(
    store, datasource
):
    typed = "I never want to see crypto pitches"
    datasource.record_feedback(
        USER,
        {
            "reel_id": "rl_003",
            "label": "not_for_me",
            "comment": typed,
            "source": "share",
        },
    )
    text = NEW_FITNESS.replace(QUOTE, typed)

    assert propose(store, datasource, text=text).error is None


def test_an_agent_cannot_add_a_date_to_the_evidence_line(
    store, datasource, with_values
):
    # Seen live: a refinement arrived with "updated 2026-11-18", a date nobody gave.
    stored = values.get_value(store, USER, "V1").text
    refined = stored.replace("- Unless: none", "- Unless: rehab progress")
    invented = refined.replace(
        "said at onboarding", "said at onboarding; updated 2026-11-18 for rehab"
    )

    refused = propose(store, datasource, text=invented, value_id="V1")
    accepted = propose(store, datasource, text=refined, value_id="V1")

    assert refused.proposal is None and "date" in refused.error
    assert accepted.error is None  # the stored approval date may stay


def test_a_new_value_may_not_carry_a_made_up_date_but_an_approval_stamp_is_ignored(
    store, datasource
):
    say(store, QUOTE)
    dated = f"{NEW_FITNESS} on 2026-11-02"  # the Evidence line is the last one
    stamped = f"{NEW_FITNESS}; approved 2026-11-14"

    assert "date" in propose(store, datasource, text=dated).error
    assert propose(store, datasource, text=stamped).error is None


def test_a_change_that_alters_nothing_but_the_evidence_line_is_refused(
    store, datasource, with_values
):
    # Seen live: the user said a value still fits, and a card arrived that only
    # added their sentence to the Evidence line.
    stored = values.get_value(store, USER, "V1").text
    only_evidence = stored.replace(
        "said at onboarding", 'said at onboarding; added: "it still fits"'
    )

    refused = propose(store, datasource, text=only_evidence, value_id="V1")

    assert refused.proposal is None and "changes nothing" in refused.error
    assert proposals.pending_for_thread(store, USER, THREAD) is None


def test_another_users_words_do_not_count(store, datasource):
    say(store, QUOTE, user=OTHER_USER)

    assert propose(store, datasource).proposal is None


def test_suggested_wording_needs_no_quote(store, datasource):
    created = propose(
        store, datasource, text=NEW_HUSTLE, evidence="fb_002, fb_004, fb_007"
    )

    assert created.error is None
    assert "Suggested wording" in created.proposal.proposed_text


def test_agreed_wording_cannot_be_claimed_in_a_new_proposal(store, datasource):
    created = propose(
        store,
        datasource,
        text=NEW_HUSTLE.replace("Suggested wording", "Agreed wording"),
    )

    assert created.proposal is None
    assert "Suggested wording" in created.error


@pytest.mark.parametrize(
    "text",
    [
        "\n".join(
            line for line in NEW_HUSTLE.splitlines() if not line.startswith("- Unless")
        ),
        NEW_HUSTLE + '\n- In my words: "second wording line"',
        "make it about hustle videos",
    ],
)
def test_malformed_text_is_refused_with_the_format(store, datasource, text):
    created = propose(store, datasource, text=text)

    assert created.proposal is None
    assert "## " in created.error


def test_unknown_target_value_is_refused(store, datasource):
    created = propose(
        store, datasource, text=NEW_HUSTLE.replace("## New:", "## V9:"), value_id="V9"
    )

    assert created.proposal is None
    assert "V9" in created.error


@pytest.mark.parametrize(
    ("value_id", "header"),
    [("new", "## V1:"), ("V1", "## New:"), ("V1", "## V2:"), ("latest", "## New:")],
)
def test_header_must_match_the_target(store, datasource, value_id, header):
    approved_value(store, datasource)

    created = propose(
        store,
        datasource,
        text=NEW_HUSTLE.replace("## New:", header),
        value_id=value_id,
        call="c2",
    )

    assert created.proposal is None


def test_evidence_ids_must_exist_for_this_user(store, datasource):
    missing = propose(store, datasource, text=NEW_HUSTLE, evidence="rl_999 and fb_002")
    in_entry = propose(
        store,
        datasource,
        text=NEW_HUSTLE.replace("fb_007", "fb_777"),
        evidence="fb_002",
    )
    existing = propose(
        store, datasource, text=NEW_HUSTLE, evidence="rl_003, fb_002, s_001"
    )

    assert missing.proposal is None and "rl_999" in missing.error
    assert in_entry.proposal is None and "fb_777" in in_entry.error
    assert existing.error is None


def test_empty_evidence_is_refused(store, datasource):
    assert propose(store, datasource, text=NEW_HUSTLE, evidence="  ").proposal is None


def test_duplicate_of_a_pending_proposal_is_refused(store, datasource):
    propose(
        store,
        datasource,
        text=NEW_HUSTLE,
        evidence="fb_002",
        thread=f"{USER}:2026-11-13",
    )

    again = propose(
        store,
        datasource,
        text=NEW_HUSTLE.upper().replace("## NEW:", "## New:"),
        evidence="fb_002",
    )

    assert again.proposal is None
    assert "already" in again.error


def test_a_rejected_change_is_not_proposed_again_in_the_same_form(store, datasource):
    first = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002")
    decide(store, first.proposal.id, action="reject")

    again = propose(
        store,
        datasource,
        text=NEW_HUSTLE.replace("  ", " "),
        evidence="fb_002",
        call="call_2",
    )

    assert again.proposal is None
    assert "rejected" in again.error


def test_only_one_proposal_may_be_pending_per_chat_thread(store, datasource):
    say(store, QUOTE)
    propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002", call="call_1")

    second = propose(store, datasource, text=NEW_FITNESS, call="call_2")

    assert second.proposal is None
    assert "decide" in second.error.lower()


def test_new_value_is_refused_at_the_cap(store, datasource):
    for number in range(values.MAX_VALUES):
        text = NEW_HUSTLE.replace("Less get-rich-quick content", f"Topic {number}")
        approved_value(store, datasource, text=text, call=f"seed_{number}")

    created = propose(
        store,
        datasource,
        text=NEW_HUSTLE.replace("Less get-rich", "One more"),
        evidence="fb_002",
    )

    assert created.proposal is None
    assert str(values.MAX_VALUES) in created.error


def test_a_change_may_carry_over_the_stored_wording_line_without_a_new_quote(
    store, datasource
):
    value_id = approved_value(store, datasource)
    stored = values.get_value(store, USER, value_id).text
    refined = stored.replace("- Unless: none", "- Unless: personal finance basics")

    created = propose(
        store,
        datasource,
        text=refined,
        value_id=value_id,
        evidence="fb_004",
        call="call_2",
    )

    assert created.error is None
    assert created.proposal.old_text == stored


# --- Deciding: the single write path ----------------------------------------------


def test_approving_a_new_value_assigns_the_id_and_writes_a_valid_entry(
    store, datasource
):
    say(store, QUOTE)
    proposal = propose(store, datasource).proposal

    result = decide(store, proposal.id)

    assert (result.ok, result.outcome, result.value_id, result.error) == (
        True,
        "approved",
        "V1",
        None,
    )
    stored = values.get_value(store, USER, "V1").text
    assert (
        stored.splitlines()[0]
        == "## V1: Fitness without body comparison (strength: high)"
    )
    assert f'- In my words: "{QUOTE}"' in stored
    assert stored.endswith("- Evidence: said at onboarding; approved 2026-11-14")
    assert values.render_values(store, USER) == stored
    assert proposals.get_proposal(store, USER, proposal.id).status == "approved"


def test_approving_suggested_wording_unchanged_stores_agreed_wording(store, datasource):
    value_id = approved_value(store, datasource)

    stored = values.get_value(store, USER, value_id).text

    assert (
        '- Agreed wording: "I don\'t want hustle and get-rich-quick videos"' in stored
    )
    assert "Suggested wording" not in stored


def test_editing_the_wording_stores_it_as_the_users_own_words(store, datasource):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal
    edited = NEW_HUSTLE.replace(
        "I don't want hustle and get-rich-quick videos", "no more money-bro stuff"
    )

    result = decide(store, proposal.id, action="edit", edited_text=edited)

    assert (result.ok, result.outcome) == (True, "edited")
    assert (
        '- In my words: "no more money-bro stuff"'
        in values.get_value(store, USER, result.value_id).text
    )


def test_editing_other_lines_but_keeping_the_suggestion_stores_agreed_wording(
    store, datasource
):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal
    edited = NEW_HUSTLE.replace("- Unless: none", "- Unless: budgeting tips")

    result = decide(store, proposal.id, action="edit", edited_text=edited)

    stored = values.get_value(store, USER, result.value_id).text
    assert "- Unless: budgeting tips" in stored
    assert "- Agreed wording:" in stored


def test_the_label_the_user_leaves_in_place_does_not_decide_the_stored_label(
    store, datasource
):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal
    relabelled = NEW_HUSTLE.replace("Suggested wording", "In my words")

    result = decide(store, proposal.id, action="edit", edited_text=relabelled)

    assert "- Agreed wording:" in values.get_value(store, USER, result.value_id).text


def test_invalid_edit_changes_nothing_and_leaves_the_card_pending(store, datasource):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal

    result = decide(
        store, proposal.id, action="edit", edited_text="hustle videos are bad"
    )

    assert not result.ok and result.error
    assert values.render_values(store, USER) == ""
    assert proposals.get_proposal(store, USER, proposal.id).status == "pending"
    assert decide(store, proposal.id).ok


@pytest.mark.parametrize(
    "edited", [NEW_HUSTLE.replace("## New:", "## V7:"), None, "   "]
)
def test_edit_cannot_retarget_the_value_or_be_empty(store, datasource, edited):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal

    result = decide(store, proposal.id, action="edit", edited_text=edited)

    assert not result.ok
    assert proposals.get_proposal(store, USER, proposal.id).status == "pending"


def test_reject_writes_nothing_and_keeps_the_record(store, datasource):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal

    result = decide(store, proposal.id, action="reject")

    assert (result.ok, result.outcome, result.value_id) == (True, "rejected", None)
    assert values.render_values(store, USER) == ""
    assert proposals.get_proposal(store, USER, proposal.id).status == "rejected"


@pytest.mark.parametrize(
    ("first", "second"),
    [("approve", "approve"), ("approve", "reject"), ("reject", "approve")],
)
def test_a_proposal_can_be_decided_only_once(store, datasource, first, second):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal
    decide(store, proposal.id, action=first)
    before = values.render_values(store, USER)

    replay = decide(store, proposal.id, action=second)

    assert not replay.ok and "already" in replay.error
    assert values.render_values(store, USER) == before
    assert len(values.list_values(store, USER)) <= 1


def test_another_user_cannot_decide_the_card(store, datasource):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal

    result = decide(store, proposal.id, user=OTHER_USER)

    assert not result.ok
    assert (
        values.render_values(store, USER) == ""
        and values.render_values(store, OTHER_USER) == ""
    )
    assert proposals.get_proposal(store, USER, proposal.id).status == "pending"


def test_unknown_proposal_and_unknown_action_return_errors(store, datasource):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal

    assert not decide(store, "p_missing").ok
    assert not decide(store, proposal.id, action="rate").ok
    assert proposals.get_proposal(store, USER, proposal.id).status == "pending"


def test_stale_proposal_is_refused_and_marked_superseded(store, datasource):
    value_id = approved_value(store, datasource)
    stored = values.get_value(store, USER, value_id).text
    first = propose(
        store,
        datasource,
        text=stored.replace("Unless: none", "Unless: budgeting tips"),
        value_id=value_id,
        evidence="fb_004",
        call="c1",
        thread=f"{USER}:2026-11-15",
    )
    second = propose(
        store,
        datasource,
        text=stored.replace("Unless: none", "Unless: student finance"),
        value_id=value_id,
        evidence="fb_007",
        call="c2",
        thread=f"{USER}:2026-11-16",
    )
    decide(store, first.proposal.id)

    result = decide(store, second.proposal.id)

    assert (result.ok, result.outcome) == (False, "superseded")
    assert "changed" in result.error
    assert "budgeting tips" in values.get_value(store, USER, value_id).text
    assert (
        proposals.get_proposal(store, USER, second.proposal.id).status == "superseded"
    )


def test_changing_a_value_keeps_its_id_and_values_render_in_order(store, datasource):
    say(store, QUOTE)
    first_id = approved_value(store, datasource)
    second_id = decide(
        store, propose(store, datasource, call="c2").proposal.id
    ).value_id
    stored = values.get_value(store, USER, first_id).text
    change = propose(
        store,
        datasource,
        text=stored.replace("Unless: none", "Unless: budgeting tips"),
        value_id=first_id,
        evidence="fb_004",
        call="c3",
    )

    result = decide(store, change.proposal.id)

    assert (first_id, second_id, result.value_id) == ("V1", "V2", "V1")
    rendered = values.render_values(store, USER)
    assert rendered.index("## V1:") < rendered.index("## V2:")
    assert "Unless: budgeting tips" in rendered
    assert [v.id for v in values.list_values(store, USER)] == ["V1", "V2"]


def test_a_value_id_is_never_reused_even_after_a_value_is_removed(store, datasource):
    say(store, QUOTE)
    approved_value(store, datasource)
    second_id = decide(
        store, propose(store, datasource, call="c2").proposal.id
    ).value_id
    store.delete(values.values_namespace(USER), second_id)

    third = propose(
        store,
        datasource,
        text=NEW_HUSTLE.replace("Less get-rich", "Fewer get-rich"),
        evidence="fb_002",
        call="c3",
    )

    assert decide(store, third.proposal.id).value_id == "V3"


def test_values_are_isolated_per_user(store, datasource):
    approved_value(store, datasource)

    assert values.has_values(store, USER)
    assert not values.has_values(store, OTHER_USER)
    assert values.render_values(store, OTHER_USER) == ""
    assert proposals.list_proposals(store, OTHER_USER) == []


def test_proposals_can_be_found_by_tool_call_and_by_thread(store, datasource):
    proposal = propose(
        store, datasource, text=NEW_HUSTLE, evidence="fb_002", call="call_7"
    ).proposal

    assert proposals.find_by_tool_call(store, USER, THREAD, "call_7") == proposal
    assert proposals.find_by_tool_call(store, USER, THREAD, "call_8") is None
    assert proposals.pending_for_thread(store, USER, THREAD) == proposal
    assert proposals.pending_for_thread(store, USER, f"{USER}:2026-11-15") is None
    decide(store, proposal.id)
    assert proposals.pending_for_thread(store, USER, THREAD) is None


def test_only_the_decision_path_calls_the_value_writer():
    package = Path(values.__file__).resolve().parent.parent
    callers = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if ".venv" not in path.parts
        and "tests" not in path.parts
        and (
            "_store_value(" in path.read_text() or "_delete_value(" in path.read_text()
        )
    }

    assert callers == {"memory/values.py", "memory/proposals.py"}


# --- Chat log and quote sources ------------------------------------------------------


def test_chat_log_keeps_user_messages_and_drops_turns_older_than_seven_days(
    store, datasource
):
    records.append_chat_turn(
        store, USER, "user", "old message", NOW - timedelta(days=8)
    )
    records.append_chat_turn(
        store, USER, "user", "recent message", NOW - timedelta(days=2)
    )
    records.append_chat_turn(store, USER, "guardian", "a reply", NOW)
    records.append_chat_turn(store, USER, "user", "today's message", NOW)

    assert records.user_chat_messages(store, USER) == [
        "recent message",
        "today's message",
    ]
    assert [
        turn["role"]
        for turn in records.chat_turns_since(store, USER, NOW - timedelta(hours=1))
    ] == [
        "guardian",
        "user",
    ]


def test_user_statements_are_chat_messages_and_typed_comments_but_not_app_comments(
    store, datasource: FakeDataSource
):
    say(store, "I scroll when I'm stressed")
    datasource.record_feedback(
        USER,
        {
            "reel_id": "rl_003",
            "label": "not_for_me",
            "comment": "typed",
            "source": "share",
        },
    )

    statements = checks.user_statements(store, datasource, USER)

    assert "I scroll when I'm stressed" in statements
    assert "typed" in statements
    assert (
        "physio before and after is different, I find it motivating" not in statements
    )
    assert checks.user_statements(store, datasource, OTHER_USER) == []


def test_quote_found_requires_a_continuous_passage():
    sources = ["rehab progress is fine, it's about getting better not looking better"]

    assert checks.quote_found("“Rehab progress is fine”", sources)
    assert checks.quote_found("it’s about getting better", sources)
    assert not checks.quote_found("rehab is about looking better", sources)
    assert not checks.quote_found("  ", sources)


def test_carried_over_wording_must_be_identical_to_the_stored_line(store, datasource):
    value_id = approved_value(store, datasource)
    stored = values.get_value(store, USER, value_id).text
    reworded = stored.replace("- Unless: none", "- Unless: budgeting tips").replace(
        "I don't want hustle", "i don't want Hustle"
    )

    created = propose(
        store,
        datasource,
        text=reworded,
        value_id=value_id,
        evidence="fb_004",
        call="c2",
    )

    assert created.proposal is None
    assert "Suggested wording" in created.error


def test_an_invalid_edit_gets_a_message_written_for_the_user_not_the_agent(
    store, datasource
):
    proposal = propose(store, datasource, text=NEW_HUSTLE, evidence="fb_002").proposal

    result = decide(store, proposal.id, action="edit", edited_text="less hustle")

    assert not result.ok
    assert "six lines" in result.error
    assert "Suggested wording" not in result.error
    assert "exact quote of the user" not in result.error
    assert len(result.error) < 300
