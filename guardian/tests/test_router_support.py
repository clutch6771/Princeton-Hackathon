"""Tests for what the Guardian router needs from the package: a read-only look at the
card waiting on a thread, the user's stated goals as onboarding background, and a
chat rate limiter the Reflector cannot use up."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from guardian import api, chat_flow, config, models
from guardian.memory import records
from guardian.tests.conftest import OTHER_USER, USER
from guardian.tests.fakes import ScriptedChatModel, reply, tool_call

THREAD = f"{USER}:2026-11-14"
GOALS = "less gym comparison stuff, it makes me feel bad"
FROM_GOALS = """## New: Less gym comparison (strength: high)
- Want more: none
- Want less: gym comparison content
- Unless: none
- In my words: "less gym comparison stuff"
- Evidence: written as my goal in the app"""


def propose(text: str = FROM_GOALS):
    args = {"value_id": "new", "new_text": text, "evidence": "written as my goal"}
    return tool_call("propose_value_change", args)


def last_user_text(model: ScriptedChatModel, call: int = 0) -> str:
    return [str(m.content) for m in model.calls[call] if isinstance(m, HumanMessage)][
        -1
    ]


# --- pending_card ---------------------------------------------------------------------


def test_pending_card_is_none_when_no_card_is_waiting(start):
    start([reply("hi")])
    api.chat(USER, THREAD, "hello")

    assert api.pending_card(USER, THREAD) is None


def test_pending_card_returns_the_card_waiting_on_the_thread(start):
    start([propose()])
    waiting = api.chat(
        USER, THREAD, "hi", {"entry": "onboarding", "stated_goals": GOALS}
    )["pending"]

    assert waiting is not None
    assert api.pending_card(USER, THREAD) == waiting
    assert api.pending_card(USER, f"{USER}:another-day") is None
    assert api.pending_card(OTHER_USER, f"{OTHER_USER}:2026-11-14") is None
    assert api.pending_card(OTHER_USER, THREAD) is None
    assert api.pending_card("not a valid id", THREAD) is None


# --- stated goals as onboarding background --------------------------------------------


def test_stated_goals_reach_the_guardian_in_the_context_block(start):
    model = start([reply("You wrote that you want less gym comparison. An example?")])

    api.chat(USER, THREAD, "hi", {"entry": "onboarding", "stated_goals": GOALS})

    sent = last_user_text(model)
    assert "entry: onboarding" in sent
    assert GOALS in sent
    assert sent.rstrip().endswith("hi")


def test_stated_goals_count_as_the_users_own_words(start, store):
    start([propose(), reply("Saved as V1.")])

    card = api.chat(USER, THREAD, "hi", {"entry": "onboarding", "stated_goals": GOALS})
    api.resume(USER, THREAD, {"action": "approve", "edited_text": None})

    assert card["error"] is None and card["pending"] is not None
    assert '- In my words: "less gym comparison stuff"' in api.get_values(USER)


def test_stated_goals_are_recorded_once_however_often_they_are_sent(start, store):
    start([reply("one"), reply("two")])
    context = {"entry": "onboarding", "stated_goals": GOALS}

    api.chat(USER, THREAD, "hi", context)
    api.chat(USER, THREAD, "hello again", context)

    said = records.user_chat_messages(store, USER)
    assert said.count(GOALS) == 1
    assert "hi" in said and "hello again" in said


@pytest.mark.parametrize("goals", [["a list"], 7, "x" * 2001])
def test_stated_goals_must_be_a_reasonably_short_string(start, goals):
    model = start([reply("unused")])

    response = api.chat(
        USER, THREAD, "hi", {"entry": "onboarding", "stated_goals": goals}
    )

    assert response["error"] == chat_flow.ERROR_BAD_CONTEXT
    assert model.calls == []


def test_blank_stated_goals_are_ignored(start, store):
    model = start([reply("What would you like less of?")])

    api.chat(USER, THREAD, "hi", {"entry": "onboarding", "stated_goals": "   "})

    assert "stated_goals" not in last_user_text(model)
    assert records.user_chat_messages(store, USER) == ["hi"]


# --- rate limiters --------------------------------------------------------------------


@pytest.fixture
def model_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv(config.API_KEY_VAR, "test-key-not-real")
    monkeypatch.delenv(models.REQUESTS_PER_MINUTE_VAR, raising=False)
    monkeypatch.delenv(models.REFLECTOR_REQUESTS_PER_MINUTE_VAR, raising=False)
    models.shared_rate_limiter.cache_clear()
    models.background_rate_limiter.cache_clear()
    yield monkeypatch
    models.shared_rate_limiter.cache_clear()
    models.background_rate_limiter.cache_clear()


def test_the_reflector_has_its_own_slower_limiter_so_chat_never_waits_behind_it(
    model_env,
):
    chat = models.build_model(config.DEFAULT_CHAT_MODEL)
    reflector = models.build_model(config.DEFAULT_REFLECTOR_MODEL, background=True)

    assert chat.rate_limiter is models.shared_rate_limiter()
    assert reflector.rate_limiter is models.background_rate_limiter()
    assert reflector.rate_limiter is not chat.rate_limiter
    assert (
        reflector.rate_limiter.requests_per_second
        == chat.rate_limiter.requests_per_second / 2
    )


def test_the_reflector_rate_can_be_set_on_its_own(model_env):
    model_env.setenv(models.REFLECTOR_REQUESTS_PER_MINUTE_VAR, "3")

    assert models.background_requests_per_minute() == 3.0


def test_a_new_limiter_starts_full_so_the_first_reply_does_not_wait(model_env):
    # LangChain starts the bucket empty: at 12 a minute the first call waited 5 s.
    chat = models.shared_rate_limiter()
    burst = [chat.acquire(blocking=False) for _ in range(int(chat.max_bucket_size) + 1)]

    assert burst == [True] * int(chat.max_bucket_size) + [False]
    assert models.background_rate_limiter().acquire(blocking=False) is True
