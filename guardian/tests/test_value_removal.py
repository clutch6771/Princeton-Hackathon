"""Tests for removing a value through chat, and for forgetting a note.

A removal goes through the same single write path as a change: a proposal the user
decides once, a stale check, per-user lookups, and an ID that is never used again.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from guardian import api, prompts, reflector
from guardian.memory import notes, proposals, records, values
from guardian.tests.conftest import NOW, OTHER_USER, USER
from guardian.tests.fakes import reply, tool_call

THREAD = f"{USER}:2026-11-14"
SAID = "please drop the news one, I don't need it"
NEW_HUSTLE = """## New: Less get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, get-rich-quick pitches
- Unless: none
- Suggested wording: "I don't want hustle and get-rich-quick videos"
- Evidence: fb_002, fb_004, fb_007"""


def propose_removal(
    store, datasource, value_id="V2", thread=THREAD, call="c1", **kwargs
):
    options = {"evidence": "asked in chat", "now": NOW, **kwargs}
    options.setdefault("source", proposals.chat_source(thread, call))
    return proposals.create_removal(
        store, datasource, USER, value_id=value_id, **options
    )


def decide(store, proposal_id, action="approve", user=USER, edited_text=None):
    return proposals.decide(
        store, user, proposal_id, action=action, edited_text=edited_text, now=NOW
    )


def ids(store, user=USER) -> list[str]:
    return [value.id for value in values.list_values(store, user)]


# --- The removal proposal and its decision -------------------------------------------------


def test_approving_a_removal_deletes_that_value_and_no_other(
    store, datasource, with_values
):
    created = propose_removal(store, datasource)

    result = decide(store, created.proposal.id)

    assert created.error is None and created.proposal.kind == proposals.KIND_REMOVAL
    assert (result.ok, result.outcome, result.value_id) == (True, "removed", "V2")
    assert ids(store) == ["V1", "V3"]
    assert "## V2:" not in values.render_values(store, USER)
    assert proposals.get_proposal(store, USER, created.proposal.id).status == "approved"


def test_rejecting_a_removal_changes_nothing(store, datasource, with_values):
    before = values.render_values(store, USER)
    created = propose_removal(store, datasource)

    result = decide(store, created.proposal.id, action="reject")

    assert (result.ok, result.outcome) == (True, "rejected")
    assert values.render_values(store, USER) == before


def test_a_removal_is_decided_once(store, datasource, with_values):
    created = propose_removal(store, datasource)
    decide(store, created.proposal.id, action="reject")

    again = decide(store, created.proposal.id)

    assert again.ok is False and "already answered" in again.error
    assert ids(store) == ["V1", "V2", "V3"]


def test_a_removal_cannot_be_edited(store, datasource, with_values):
    created = propose_removal(store, datasource)

    result = decide(store, created.proposal.id, action="edit", edited_text="anything")

    assert result.ok is False and "approve or reject" in result.error
    assert ids(store) == ["V1", "V2", "V3"]
    assert proposals.get_proposal(store, USER, created.proposal.id).status == "pending"


def test_a_removal_is_stale_if_the_value_changed_after_it_was_proposed(
    store, datasource, with_values
):
    removal = propose_removal(store, datasource)
    changed = values.get_value(store, USER, "V2").text.replace(
        "- Unless: none", "- Unless: breaking local news"
    )
    change = proposals.create_proposal(
        store,
        datasource,
        USER,
        value_id="V2",
        new_text=changed,
        evidence="asked in chat",
        source=proposals.reflector_source("s_001", "r1"),
        now=NOW,
    )
    decide(store, change.proposal.id)

    result = decide(store, removal.proposal.id)

    assert (result.ok, result.outcome) == (False, "superseded")
    assert "breaking local news" in values.get_value(store, USER, "V2").text


def test_a_removal_belongs_to_one_user(store, datasource, with_values):
    created = propose_removal(store, datasource)

    other = decide(store, created.proposal.id, user=OTHER_USER)

    assert other.ok is False and "does not exist" in other.error
    assert ids(store) == ["V1", "V2", "V3"]


def test_only_a_stored_value_can_be_removed(store, datasource, with_values):
    for value_id in ("V9", "new", "", "v2 or 1=1"):
        refused = propose_removal(store, datasource, value_id=value_id)
        assert refused.proposal is None and refused.error, value_id


def test_a_removed_values_id_is_never_used_again(store, datasource, with_values):
    removal = propose_removal(store, datasource, value_id="V3")
    decide(store, removal.proposal.id)
    added = proposals.create_proposal(
        store,
        datasource,
        USER,
        value_id="new",
        new_text=NEW_HUSTLE,
        evidence="fb_002, fb_004, fb_007",
        source=proposals.reflector_source("s_001", "r1"),
        now=NOW,
    )

    result = decide(store, added.proposal.id)

    assert result.value_id == "V4" and ids(store) == ["V1", "V2", "V4"]


def test_the_reflector_cannot_propose_a_removal(store, datasource, with_values):
    refused = propose_removal(
        store, datasource, source=proposals.reflector_source("s_001", "r1")
    )

    assert refused.proposal is None and "Only the Guardian" in refused.error
    assert "propose_value_removal" not in reflector.ALLOWED_TOOLS
    assert "forget_note" not in reflector.ALLOWED_TOOLS


def test_a_removal_needs_evidence_that_exists(store, datasource, with_values):
    assert propose_removal(store, datasource, evidence="  ").proposal is None
    assert "fb_999" in propose_removal(store, datasource, evidence="fb_999").error


def test_one_card_at_a_time_in_a_conversation(store, datasource, with_values):
    propose_removal(store, datasource, value_id="V2", call="c1")

    second = propose_removal(store, datasource, value_id="V3", call="c2")

    assert second.proposal is None and "already waiting" in second.error


def test_a_change_that_empties_a_value_is_refused_in_favour_of_a_removal(
    store, datasource, with_values
):
    stored = values.get_value(store, USER, "V2").text
    lines = stored.splitlines()
    emptied = "\n".join(
        [
            lines[0],
            "- Want more: none",
            "- Want less: None",
            "- Unless: none",
            *lines[4:],
        ]
    )

    refused = proposals.create_proposal(
        store,
        datasource,
        USER,
        value_id="V2",
        new_text=emptied,
        evidence="asked in chat",
        source=proposals.chat_source(THREAD, "c1"),
        now=NOW,
    )

    assert refused.proposal is None and "propose_value_removal" in refused.error


# --- Through the chat ------------------------------------------------------------------------


def removal_call(value_id: str = "V2", call: str = "call_1"):
    args = {"value_id": value_id, "evidence": "asked in chat"}
    return tool_call("propose_value_removal", args, call)


def test_the_guardian_proposes_a_removal_and_approval_deletes_the_value(
    start, with_values, store
):
    start([removal_call(), reply("Done. I've removed V2.")])

    card = api.chat(USER, THREAD, SAID)
    resumed = api.resume(USER, THREAD, {"action": "approve", "edited_text": None})

    assert card["pending"]["kind"] == "value_removal"
    assert card["pending"]["value_change"]["value_id"] == "V2"
    assert card["pending"]["value_change"]["old_text"].startswith(
        "## V2: News without outrage"
    )
    assert card["pending"]["value_change"]["new_text"] == ""
    # The reply names the removed value; the citation check must not replace it.
    assert resumed == {
        "reply": "Done. I've removed V2.",
        "pending": None,
        "error": None,
    }
    assert "## V2:" not in api.get_values(USER)
    assert "## V2:" not in api.get_memory(USER)["values.md"]
    assert (
        len([p for p in proposals.list_proposals(store, USER) if p.kind == "removal"])
        == 1
    )


def test_rejecting_the_removal_in_chat_keeps_the_value(start, with_values):
    model = start([removal_call(), reply("Okay, V2 stays.")])
    api.chat(USER, THREAD, SAID)

    resumed = api.resume(USER, THREAD, {"action": "reject", "edited_text": None})

    told = [m for m in model.calls[1] if isinstance(m, ToolMessage)][-1]
    assert "rejected" in str(told.content) and resumed["reply"] == "Okay, V2 stays."
    assert "## V2:" in api.get_values(USER)


def test_an_edit_of_a_removal_card_is_refused_and_the_card_stays(start, with_values):
    start([removal_call()])
    card = api.chat(USER, THREAD, SAID)["pending"]

    edited = api.resume(USER, THREAD, {"action": "edit", "edited_text": "x"})

    assert edited["pending"] == card and "approve or reject" in edited["error"]


def test_the_guardian_is_told_when_the_value_to_remove_does_not_exist(
    start, with_values
):
    model = start([removal_call("V9"), reply("I can't find that value.")])

    response = api.chat(USER, THREAD, SAID)

    told = [m for m in model.calls[1] if isinstance(m, ToolMessage)][-1]
    assert response["pending"] is None and "V9" in str(told.content)


# --- Forgetting a note -----------------------------------------------------------------------


def save(store, datasource, said: str) -> str:
    records.append_chat_turn(store, USER, records.ROLE_USER, said, NOW)
    return notes.save_note(store, datasource, USER, said, said, NOW).note_id


def test_forget_note_deletes_that_note_and_says_which_one(
    start, store, datasource, with_values
):
    first = save(store, datasource, "I scroll when I'm stressed before exams")
    second = save(store, datasource, "I train three times a week")
    model = start([tool_call("forget_note", {"note_id": first}), reply("Forgotten.")])

    api.chat(USER, THREAD, "please forget what I said about exams")

    told = str([m for m in model.calls[1] if isinstance(m, ToolMessage)][-1].content)
    assert first in told and "stressed before exams" in told
    assert [note.id for note in notes.list_notes(store, USER)] == [second]
    assert "exams" not in api.get_memory(USER)["notes.md"]


def test_a_forgotten_notes_id_is_not_reused_and_unknown_notes_are_refused(
    start, store, datasource, with_values
):
    first = save(store, datasource, "I scroll when I'm stressed before exams")
    model = start(
        [
            tool_call("forget_note", {"note_id": first}, "c1"),
            tool_call("forget_note", {"note_id": "N9"}, "c2"),
            reply("Done."),
        ]
    )
    api.chat(USER, THREAD, "forget that note")

    again = save(store, datasource, "I train three times a week")

    told = str([m for m in model.calls[2] if isinstance(m, ToolMessage)][-1].content)
    assert again != first and "N9" in told and "Refused" in told


def test_a_note_can_only_be_forgotten_by_its_owner(store, datasource):
    note_id = save(store, datasource, "I scroll when I'm stressed before exams")

    assert notes.forget_note(store, OTHER_USER, note_id) is None
    assert [note.id for note in notes.list_notes(store, USER)] == [note_id]


def test_the_prompt_says_when_to_remove_a_value_and_when_to_forget_a_note():
    text = " ".join(prompts.GUARDIAN_PROMPT.split())

    assert "asks to delete or drop a value, call propose_value_removal" in text
    assert "explicitly asks you to forget" in text
    assert "Never delete a note without" in text


# --- Found by the review of this change -------------------------------------------------


def test_ordinary_proposals_are_stored_without_the_new_field(
    store, datasource, with_values
):
    # Code from before removals existed must still be able to read them.
    from guardian.memory import keys

    removal = propose_removal(store, datasource)
    stored = {
        item.key: item.value
        for item in keys.items(store, keys.namespace(USER, keys.PROPOSALS))
    }

    changes = [record for key, record in stored.items() if key != removal.proposal.id]
    assert changes and all("kind" not in record for record in changes)
    assert stored[removal.proposal.id]["kind"] == "removal"
    assert all(
        p.kind in ("change", "removal") for p in proposals.list_proposals(store, USER)
    )


def test_removing_a_value_closes_other_cards_about_it(store, datasource, with_values):
    changed = values.get_value(store, USER, "V2").text.replace(
        "- Unless: none", "- Unless: breaking local news"
    )
    waiting = proposals.create_proposal(
        store,
        datasource,
        USER,
        value_id="V2",
        new_text=changed,
        evidence="asked in chat",
        source=proposals.reflector_source("s_001", "r1"),
        now=NOW,
    )
    removal = propose_removal(store, datasource)

    decide(store, removal.proposal.id)

    assert (
        proposals.get_proposal(store, USER, waiting.proposal.id).status == "superseded"
    )


def test_other_spellings_of_none_are_still_an_emptied_value(
    store, datasource, with_values
):
    lines = values.get_value(store, USER, "V2").text.splitlines()
    for word in ("none.", '"none"', "(none)", "None!"):
        emptied = "\n".join(
            [
                lines[0],
                f"- Want more: {word}",
                f"- Want less: {word}",
                f"- Unless: {word}",
                *lines[4:],
            ]
        )
        refused = proposals.create_proposal(
            store,
            datasource,
            USER,
            value_id="V2",
            new_text=emptied,
            evidence="asked in chat",
            source=proposals.chat_source(THREAD, "c1"),
            now=NOW,
        )
        assert refused.proposal is None and "removal" in refused.error, word


def test_a_removal_with_no_counter_record_still_retires_the_id(
    store, datasource, with_values
):
    from guardian.memory import keys

    store.delete(keys.namespace(USER, keys.META), values.COUNTER_KEY)
    removal = propose_removal(store, datasource, value_id="V3")
    decide(store, removal.proposal.id)
    added = proposals.create_proposal(
        store,
        datasource,
        USER,
        value_id="new",
        new_text=NEW_HUSTLE,
        evidence="fb_002, fb_004, fb_007",
        source=proposals.reflector_source("s_001", "r1"),
        now=NOW,
    )

    assert decide(store, added.proposal.id).value_id == "V4"


def test_a_waiting_removal_card_asks_for_approve_or_reject_only(start, with_values):
    start([removal_call()])
    api.chat(USER, THREAD, SAID)

    again = api.chat(USER, THREAD, "hello?")

    assert again["pending"]["kind"] == "value_removal"
    assert again["error"] == "Please approve or reject the removal first."


def test_a_note_is_forgotten_only_when_the_user_asked_in_this_message(
    start, store, datasource, with_values
):
    # The rule is checked in code, not only stated in the prompt.
    note_id = save(store, datasource, "I scroll when I'm stressed before exams")
    model = start([tool_call("forget_note", {"note_id": note_id}), reply("Okay.")])

    api.chat(USER, THREAD, "what are my values again?")

    told = str([m for m in model.calls[1] if isinstance(m, ToolMessage)][-1].content)
    assert "Refused" in told and "explicitly" in told
    assert [note.id for note in notes.list_notes(store, USER)] == [note_id]


def test_forget_note_refuses_a_malformed_id_without_crashing(
    start, store, datasource, with_values
):
    note_id = save(store, datasource, "I scroll when I'm stressed before exams")
    calls = [
        tool_call("forget_note", {"note_id": bad}, f"c{n}")
        for n, bad in enumerate(["", "N", "Nx", "N99"])
    ]
    model = start([*calls, reply("Done.")])

    api.chat(USER, THREAD, "please forget that")

    results = [str(m.content) for m in model.calls[-1] if isinstance(m, ToolMessage)]
    assert len(results) == 4 and all("Refused" in result for result in results)
    assert [note.id for note in notes.list_notes(store, USER)] == [note_id]
