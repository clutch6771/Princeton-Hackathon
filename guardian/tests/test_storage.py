"""Tests for storage selection, locks, and the guarantees that must hold on Postgres.

Tests marked `postgres` need TEST_DATABASE_URL, which must point only at a disposable
database. They never use DATABASE_URL.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from guardian import api, config, storage
from guardian.memory import inbox, locks, proposals, values
from guardian.tests.conftest import (
    NOW,
    OTHER_USER,
    TEST_DATABASE_URL_VAR,
    USER,
    clean_test_users,
    disposable_database_url,
)
from guardian.tests.fakes import ScriptedChatModel, reply, tool_call

THREAD = f"{USER}:2026-11-14"
QUOTE = "gym stuff is great, comparing bodies is not"
FITNESS = f"""## New: Fitness without body comparison (strength: high)
- Want more: none
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said in chat"""


def hustle(title: str = "Less get-rich-quick content") -> dict[str, str]:
    text = f"""## New: {title} (strength: medium)
- Want more: none
- Want less: hustle culture, get-rich-quick pitches
- Unless: none
- Suggested wording: "I don't want hustle and get-rich-quick videos"
- Evidence: fb_002, fb_004, fb_007"""
    return {"value_id": "new", "new_text": text, "evidence": "fb_002, fb_004, fb_007"}


def propose(args: dict[str, str], call: str):
    return tool_call("propose_value_change", args, call)


# --- Selection and secrets (no database needed) ----------------------------------------


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    for name in (storage.DATABASE_URL_VAR, storage.GUARDIAN_DATABASE_URL_VAR):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_storage_is_in_memory_when_no_connection_string_is_set(no_env):
    opened = storage.open_from_env()

    assert opened.kind == "memory"
    assert isinstance(opened.store, InMemoryStore) and isinstance(
        opened.checkpointer, InMemorySaver
    )
    assert isinstance(opened.locks, locks.LocalLocks)


@pytest.mark.parametrize(
    ("database", "guardian_database", "expected"),
    [
        ("postgres://a", "", "postgres://a"),
        ("postgres://a", "postgres://b", "postgres://b"),
        ("   ", "", None),
        ("", "", None),
    ],
)
def test_guardian_database_url_overrides_database_url(
    no_env, database, guardian_database, expected
):
    no_env.setenv(storage.DATABASE_URL_VAR, database)
    no_env.setenv(storage.GUARDIAN_DATABASE_URL_VAR, guardian_database)

    assert storage.database_url() == expected


def test_connection_strings_and_passwords_are_redacted(no_env):
    url = "postgres://guardian:s3cr3t-pass@db.example.com:5432/app"
    no_env.setenv(storage.DATABASE_URL_VAR, url)

    redacted = config.redact_secrets(
        f'connection to "{url}" failed; password s3cr3t-pass rejected'
    )

    assert url not in redacted and "s3cr3t-pass" not in redacted
    assert "failed" in redacted


def test_configure_without_a_store_uses_storage_from_the_environment(
    no_env, datasource, clock
):
    api.configure(
        datasource=datasource,
        clock=clock,
        chat_model=ScriptedChatModel(script=[reply("hi")]),
    )

    assert api.get_values(USER) == ""
    assert api.chat(USER, THREAD, "hello")["reply"] == "hi"


def test_configure_requires_a_datasource(no_env):
    with pytest.raises(config.ConfigError):
        api.configure()


def test_local_locks_exclude_a_second_holder_of_the_same_key():
    local = locks.LocalLocks()

    with local.attempt("guardian:reflect:u1") as first:
        with local.attempt("guardian:reflect:u1") as second, local.attempt(
            "guardian:reflect:u2"
        ) as other:
            assert (first, second, other) == (True, False, True)
    with local.attempt("guardian:reflect:u1") as again:
        assert again is True


# --- On Postgres --------------------------------------------------------------------------

postgres = pytest.mark.postgres


@pytest.fixture
def url() -> str:
    value = disposable_database_url()
    if not value:
        pytest.skip(f"{TEST_DATABASE_URL_VAR} is not set")
    return value


@pytest.fixture
def two_processes(url):
    """Two independent connections to the same database, standing in for two server processes."""
    first, second = storage.open_postgres(url), storage.open_postgres(url)
    clean_test_users(first)
    yield first, second
    clean_test_users(first)
    first.close()
    second.close()


@postgres
def test_postgres_locks_exclude_another_process(two_processes):
    first, second = two_processes

    with first.locks.attempt("guardian:reflect:demo-user") as held:
        with second.locks.attempt("guardian:reflect:demo-user") as blocked:
            with second.locks.attempt("guardian:reflect:someone-else") as other:
                assert (held, blocked, other) == (True, False, True)
    with second.locks.attempt("guardian:reflect:demo-user") as after_release:
        assert after_release is True


@postgres
def test_the_judges_select_finds_approved_values_and_nothing_else(
    two_processes, datasource
):
    # Compatibility contract with watcher_Agent/judge/values/postgres_source.py.
    opened, _ = two_processes
    locks.set_provider(opened.locks)
    for index, change in enumerate([hustle("First"), hustle("Second")]):
        created = proposals.create_proposal(
            opened.store,
            datasource,
            USER,
            source=proposals.reflector_source("s_001", f"r{index}"),
            now=NOW,
            **change,
        )
        proposals.decide(
            opened.store,
            USER,
            created.proposal.id,
            action="approve",
            edited_text=None,
            now=NOW,
        )
    proposals.create_proposal(
        opened.store,
        datasource,
        USER,
        source=proposals.reflector_source("s_001", "r9"),
        now=NOW,
        **hustle("Pending"),
    )

    with opened.pool.connection() as connection:
        rows = connection.execute(
            "SELECT key, value, updated_at FROM public.store WHERE prefix = %s "
            "AND (expires_at IS NULL OR expires_at > now())",
            (f"{USER}.guardian.values",),
        ).fetchall()

    assert sorted(row["key"] for row in rows) == ["V1", "V2"]
    for row in rows:
        assert isinstance(row["value"]["text"], str)
        assert row["value"]["text"].startswith(f"## {row['key']}:")
    joined = "\n\n".join(
        row["value"]["text"] for row in sorted(rows, key=lambda r: int(r["key"][1:]))
    )
    assert joined == values.render_values(opened.store, USER)


@postgres
def test_a_user_id_with_a_wildcard_character_cannot_read_another_users_records(
    two_processes, datasource
):
    opened, _ = two_processes
    locks.set_provider(opened.locks)
    for user in ("a_b", "aXb"):
        opened.store.put(
            values.values_namespace(user),
            "V1",
            {"text": f"## V1: {user}", "updated": "x"},
        )

    assert [value.text for value in values.list_values(opened.store, "a_b")] == [
        "## V1: a_b"
    ]
    assert [value.text for value in values.list_values(opened.store, "aXb")] == [
        "## V1: aXb"
    ]
    assert values.list_values(opened.store, OTHER_USER) == []


@postgres
def test_a_card_is_decided_once_even_from_two_processes(two_processes, datasource):
    first, second = two_processes
    locks.set_provider(first.locks)
    created = proposals.create_proposal(
        first.store,
        datasource,
        USER,
        source=proposals.reflector_source("s_001", "r1"),
        now=NOW,
        **hustle(),
    )
    start = threading.Barrier(2)

    def decide(opened: storage.Storage) -> bool:
        start.wait()
        result = proposals.decide(
            opened.store,
            USER,
            created.proposal.id,
            action="approve",
            edited_text=None,
            now=NOW,
        )
        return result.ok

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, [first, second]))

    assert sorted(outcomes) == [False, True]
    assert [value.id for value in values.list_values(first.store, USER)] == ["V1"]


@postgres
def test_value_ids_are_unique_under_concurrent_approvals(two_processes, datasource):
    first, second = two_processes
    locks.set_provider(first.locks)
    created = [
        proposals.create_proposal(
            first.store,
            datasource,
            USER,
            source=proposals.reflector_source("s_001", f"r{n}"),
            now=NOW,
            **hustle(f"Topic {n}"),
        ).proposal
        for n in range(6)
    ]

    def approve(pair) -> str | None:
        index, proposal = pair
        opened = first if index % 2 == 0 else second
        return proposals.decide(
            opened.store, USER, proposal.id, action="approve", edited_text=None, now=NOW
        ).value_id

    with ThreadPoolExecutor(max_workers=6) as pool:
        assigned = list(pool.map(approve, enumerate(created)))

    assert sorted(assigned, key=lambda value_id: int(value_id[1:])) == [
        f"V{n}" for n in range(1, 7)
    ]
    assert len(values.list_values(first.store, USER)) == 6


@postgres
def test_an_approved_value_and_a_pending_card_survive_a_restart(url, datasource, clock):
    opened = storage.open_postgres(url)
    clean_test_users(opened)
    script = [
        propose(
            {"value_id": "new", "new_text": FITNESS, "evidence": "said in chat"}, "c1"
        ),
        reply("Saved."),
        propose(hustle(), "c2"),
    ]
    api.configure(
        opened.store,
        opened.checkpointer,
        datasource,
        clock=clock,
        chat_model=ScriptedChatModel(script=script),
        locks=opened.locks,
    )
    api.chat(USER, THREAD, f"Honestly, {QUOTE}.")
    api.resume(USER, THREAD, {"action": "approve", "edited_text": None})
    waiting = api.chat(USER, THREAD, "and fewer money videos please")["pending"]
    inbox.add_card(
        opened.store,
        inbox.new_card(
            USER, "learned", "- A bullet. [s_001]", NOW, card_id="learned_s_001"
        ),
    )
    opened.close()

    reopened = storage.open_postgres(url)
    api.configure(
        reopened.store,
        reopened.checkpointer,
        datasource,
        clock=clock,
        chat_model=ScriptedChatModel(script=[reply("Saved that too.")]),
        locks=reopened.locks,
    )
    try:
        assert api.get_values(USER).startswith("## V1: Fitness without body comparison")
        assert api.chat(USER, THREAD, "hello?")["pending"] == waiting
        assert [q["id"] for q in api.get_questions(USER)] == ["learned_s_001"]

        resumed = api.resume(USER, THREAD, {"action": "approve", "edited_text": None})

        assert resumed == {"reply": "Saved that too.", "pending": None, "error": None}
        assert "## V2: Less get-rich-quick content" in api.get_values(USER)
    finally:
        clean_test_users(reopened)
        reopened.close()
