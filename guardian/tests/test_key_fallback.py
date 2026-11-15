"""Tests for fallback Gemini keys (GOOGLE_API_KEY_FALLBACKS). A scripted model raises
the quota error, so nothing here calls an API."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from guardian import api, chat_agent, chat_flow, config, key_fallback, models
from guardian.tests.conftest import USER
from guardian.tests.fakes import (
    QUOTA_ERROR,
    FailingChatModel,
    QuotaChatModel,
    ScriptedChatModel,
    reply,
)
from guardian.tests.test_reflect_pending import good_run

THREAD = f"{USER}:2026-11-14"
SECOND = "fallback-key-two-not-real"
THIRD = "fallback-key-three-not-real"


@pytest.fixture
def keys(monkeypatch, tmp_path):
    """A primary key and two fallbacks, and a record of which key each model was built with."""
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv(config.API_KEY_VAR, "primary-key-not-real")
    monkeypatch.setenv(key_fallback.FALLBACKS_VAR, f"{SECOND}, {THIRD}")
    built: dict[str, Any] = {}

    def serve(by_key: dict[str, Any]) -> dict[str, Any]:
        def fake_build(name: str, background: bool = False, api_key: str | None = None):
            built[api_key or "primary"] = by_key[api_key]
            return by_key[api_key]

        monkeypatch.setattr(models, "build_model", fake_build)
        return built

    return serve


def configure(store, datasource, clock, model: Any) -> None:
    api.configure(store, InMemorySaver(), datasource, clock=clock, chat_model=model)


def test_fallback_keys_are_read_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv(key_fallback.FALLBACKS_VAR, " k2 ,k3,, ")
    assert key_fallback.fallback_keys() == ["k2", "k3"]
    monkeypatch.delenv(key_fallback.FALLBACKS_VAR)
    assert key_fallback.fallback_keys() == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (QUOTA_ERROR, True),
        ("429 Too Many Requests", True),
        ("RESOURCE_EXHAUSTED", True),
        ("You exceeded your current quota", True),
        ("requests per day limit exceeded", True),
        ("rate limit reached", True),
        ("503 The model is overloaded", False),
        ("model unavailable", False),
        ("invalid argument", False),
    ],
)
def test_quota_and_rate_limit_errors_are_recognised(message, expected):
    assert key_fallback.is_quota_error(RuntimeError(message)) is expected


def test_a_call_that_runs_out_of_quota_succeeds_on_the_second_key(
    store, datasource, clock, keys, caplog
):
    primary = QuotaChatModel()
    second = ScriptedChatModel(script=[reply("Hello from the second key.")])
    keys({SECOND: second})
    configure(store, datasource, clock, primary)

    with caplog.at_level(logging.WARNING, logger="guardian"):
        response = api.chat(USER, THREAD, "hi")

    assert response == {
        "reply": "Hello from the second key.",
        "pending": None,
        "error": None,
    }
    assert key_fallback.position() == 1
    # The switch comes only after the ordinary retries on the first key have failed.
    assert len(primary.attempts) == chat_agent.MODEL_RETRIES + 1
    assert "switched to fallback key 1" in caplog.text
    assert SECOND not in caplog.text and "primary-key-not-real" not in caplog.text


def test_later_calls_stay_on_the_new_key(store, datasource, clock, keys):
    primary = QuotaChatModel()
    second = ScriptedChatModel(script=[reply("One."), reply("Two.")])
    keys({SECOND: second})
    configure(store, datasource, clock, primary)
    api.chat(USER, THREAD, "hi")
    used_before = len(primary.attempts)

    again = api.chat(USER, THREAD, "and again")

    assert again["reply"] == "Two."
    assert len(primary.attempts) == used_before  # the first key is not tried again
    assert key_fallback.position() == 1


def test_a_second_exhausted_key_moves_on_to_the_third(store, datasource, clock, keys):
    third = ScriptedChatModel(script=[reply("Hello from the third key.")])
    keys({SECOND: QuotaChatModel(), THIRD: third})
    configure(store, datasource, clock, QuotaChatModel())

    first = api.chat(USER, THREAD, "hi")  # the first key fails, and so does the second
    second = api.chat(
        USER, THREAD, "hi again"
    )  # the second key is skipped for the third

    assert first["error"] == chat_flow.ERROR_UNAVAILABLE
    assert second["reply"] == "Hello from the third key."
    assert key_fallback.position() == 2


def test_when_every_key_is_exhausted_the_call_fails_as_before(
    store, datasource, clock, keys
):
    keys({SECOND: QuotaChatModel(), THIRD: QuotaChatModel()})
    configure(store, datasource, clock, QuotaChatModel())

    responses = [api.chat(USER, THREAD, "hi") for _ in range(3)]

    assert all(
        r == {"reply": None, "pending": None, "error": chat_flow.ERROR_UNAVAILABLE}
        for r in responses
    )
    assert key_fallback.position() == 2  # the last key; there is nowhere left to go


def test_with_no_fallback_keys_a_quota_error_fails_as_before(
    store, datasource, clock, keys, monkeypatch
):
    monkeypatch.delenv(key_fallback.FALLBACKS_VAR)
    built = keys({})
    configure(store, datasource, clock, QuotaChatModel())

    assert api.chat(USER, THREAD, "hi")["error"] == chat_flow.ERROR_UNAVAILABLE
    assert built == {} and key_fallback.position() == 0


def test_an_error_that_is_not_about_quota_does_not_switch_keys(
    store, datasource, clock, keys
):
    built = keys({SECOND: ScriptedChatModel(script=[reply("unused")])})
    configure(store, datasource, clock, FailingChatModel())

    assert api.chat(USER, THREAD, "hi")["error"] == chat_flow.ERROR_UNAVAILABLE
    assert built == {} and key_fallback.position() == 0


def test_the_reflector_switches_keys_too(store, clock, keys):
    from guardian.tests.test_reflect_pending import one_session_source

    second = ScriptedChatModel(script=good_run())
    keys({SECOND: second})
    api.configure(
        store,
        InMemorySaver(),
        one_session_source(clock),
        clock=clock,
        reflector_model=QuotaChatModel(),
    )

    assert api.reflect_pending(USER) == 1
    assert key_fallback.position() == 1 and len(second.calls) == len(good_run())


def test_fallback_keys_are_redacted_like_the_others(keys):
    text = config.redact_secrets(
        f"failed with {SECOND} then {THIRD} and primary-key-not-real"
    )

    assert (
        SECOND not in text and THIRD not in text and "primary-key-not-real" not in text
    )


def test_a_model_can_be_built_with_a_given_key(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setenv(config.API_KEY_VAR, "primary-key-not-real")

    model = models.build_model(config.DEFAULT_CHAT_MODEL, api_key=SECOND)

    assert model.google_api_key.get_secret_value() == SECOND
