"""Tests for the demo reset command. They run only on the disposable test database
(TEST_DATABASE_URL); the command is never tested against a real one."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from guardian import storage
from guardian.datasource import FakeDataSource
from guardian.memory import inbox, proposals, records, values
from guardian.seed.install import install_initial_values
from guardian.tests.conftest import (
    TEST_DATABASE_URL_VAR,
    clean_test_users,
    disposable_database_url,
)
from guardian_router import reset_demo

NOW = datetime(2026, 11, 14, 21, 0, tzinfo=UTC)
USER = "demo-user"
OTHERS = ("someone-else", "a_b", "aXb")  # "a_b" and "aXb" differ only by a SQL wildcard
JUDGE_OWNED = (
    "judge.sessions",
    "judge.session_events",
    "judge.user_notification_state",
)


@pytest.fixture
def database(monkeypatch):
    """The disposable database with Guardian's tables and the Judge's schema."""
    url = disposable_database_url()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_VAR} is not set")
    migrator = pytest.importorskip("judge.persistence.migrator")
    migrator.apply(url, without_vector=not migrator.vector_extension_available(url))
    monkeypatch.setenv(TEST_DATABASE_URL_VAR, url)
    opened = storage.open_postgres(url)
    wipe(opened)
    yield opened
    wipe(opened)
    opened.close()


def wipe(opened: storage.Storage) -> None:
    clean_test_users(opened)
    with opened.pool.connection() as connection:
        for table in JUDGE_OWNED:
            connection.execute(
                f"DELETE FROM {table} WHERE owner_id = ANY(%s)",  # noqa: S608 - fixed names
                ([USER, *OTHERS],),
            )


def fill(opened: storage.Storage, user: str) -> None:
    """Give a user Guardian records of every kind and a few Judge rows."""
    datasource = FakeDataSource.from_seed(user, clock=lambda: NOW)
    install_initial_values(opened.store, datasource, user, NOW)
    records.append_chat_turn(opened.store, user, records.ROLE_USER, "hello", NOW)
    inbox.add_card(opened.store, inbox.new_card(user, "learned", "- A bullet.", NOW))
    opened.checkpointer.put(
        {"configurable": {"thread_id": f"{user}:2026-11-14", "checkpoint_ns": ""}},
        {
            "v": 1,
            "id": str(uuid.uuid4()),
            "ts": NOW.isoformat(),
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        },
        {"source": "input", "step": 0, "parents": {}},
        {},
    )
    session_id = uuid.uuid4()
    with opened.pool.connection() as connection:
        connection.execute(
            "INSERT INTO judge.sessions (session_id, owner_id, source_id, started_at,"
            " last_ingest_at) VALUES (%s, %s, 'test', %s, %s)",
            (session_id, user, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO judge.session_events (event_id, session_id, owner_id,"
            " event_type, payload, created_at) VALUES (%s, %s, %s, 'test', '{}', %s)",
            (uuid.uuid4(), session_id, user, NOW),
        )
        connection.execute(
            "INSERT INTO judge.user_notification_state (owner_id) VALUES (%s)", (user,)
        )


def rows_left(opened: storage.Storage, user: str) -> dict[str, int]:
    with opened.pool.connection() as connection:
        counts = {
            "store": connection.execute(
                "SELECT count(*) AS n FROM store WHERE split_part(prefix, '.', 1) = %s",
                (user,),
            ).fetchone()["n"],
            "checkpoints": connection.execute(
                "SELECT count(*) AS n FROM checkpoints"
                " WHERE split_part(thread_id, ':', 1) = %s",
                (user,),
            ).fetchone()["n"],
        }
        for table in JUDGE_OWNED:
            counts[table] = connection.execute(
                f"SELECT count(*) AS n FROM {table} WHERE owner_id = %s",  # noqa: S608
                (user,),
            ).fetchone()["n"]
    return counts


def run(*arguments: str) -> int:
    return reset_demo.main(["--database-env", TEST_DATABASE_URL_VAR, *arguments])


def test_without_yes_it_lists_what_would_go_and_deletes_nothing(database, capsys):
    fill(database, USER)
    before = rows_left(database, USER)

    code = run("--user", USER)

    printed = capsys.readouterr().out
    assert code == 1
    assert "Nothing was deleted" in printed and "--yes" in printed
    assert USER in printed and "store" in printed and "judge.sessions" in printed
    assert rows_left(database, USER) == before
    assert all(count > 0 for count in before.values())


def test_with_yes_it_clears_that_users_guardian_records_and_judge_rows(
    database, capsys
):
    fill(database, USER)

    code = run("--user", USER, "--yes")

    assert code == 0
    assert rows_left(database, USER) == dict.fromkeys(rows_left(database, USER), 0)
    assert values.list_values(database.store, USER) == []
    assert proposals.list_proposals(database.store, USER) == []
    assert "Deleted" in capsys.readouterr().out


def test_other_users_are_left_alone(database):
    for user in (USER, *OTHERS):
        fill(database, user)
    before = {user: rows_left(database, user) for user in OTHERS}

    assert run("--user", "a_b", "--yes") == 0

    assert rows_left(database, "a_b") == dict.fromkeys(before["a_b"], 0)
    for user in ("someone-else", "aXb"):
        assert rows_left(database, user) == before[user]
    assert len(values.list_values(database.store, USER)) == 3


def test_running_it_twice_is_harmless(database):
    fill(database, USER)

    assert run("--user", USER, "--yes") == 0
    assert run("--user", USER, "--yes") == 0


@pytest.mark.parametrize("user", ["", "not a valid id", "demo-user; DROP", "%", "a.b"])
def test_an_invalid_user_id_is_refused_before_anything_is_opened(user, capsys):
    assert reset_demo.main(["--user", user, "--yes"]) == 2
    assert "letters, digits" in capsys.readouterr().err


def test_the_user_must_be_named():
    with pytest.raises(SystemExit):
        reset_demo.main(["--yes"])


def test_it_refuses_when_no_database_is_configured(monkeypatch, capsys):
    monkeypatch.delenv("NO_SUCH_DATABASE_URL", raising=False)

    code = reset_demo.main(["--user", USER, "--database-env", "NO_SUCH_DATABASE_URL"])

    assert code == 2
    assert "NO_SUCH_DATABASE_URL" in capsys.readouterr().err
