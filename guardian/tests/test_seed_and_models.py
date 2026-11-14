"""Tests for installing the persona's initial values and for the model factory."""

from __future__ import annotations

import pytest

from guardian import config, models
from guardian.memory import formats, proposals, values
from guardian.seed.install import install_initial_values
from guardian.tests.conftest import NOW, OTHER_USER, USER


def test_initial_values_are_installed_through_the_approval_path(store, datasource):
    ids = install_initial_values(store, datasource, USER, NOW)

    assert ids == ["V1", "V2", "V3"]
    entries = [formats.parse_entry(value.text).entry for value in values.list_values(store, USER)]
    assert [entry.title for entry in entries] == [
        "Fitness without body comparison", "News without outrage", "Cooking I can actually make",
    ]
    assert all(entry.wording_label == formats.WORDING_OWN for entry in entries)
    assert [p.status for p in proposals.list_proposals(store, USER)] == ["approved"] * 3
    assert not values.has_values(store, OTHER_USER)


def test_installing_twice_does_not_duplicate_values(store, datasource):
    install_initial_values(store, datasource, USER, NOW)

    ids = install_initial_values(store, datasource, USER, NOW)

    assert ids == ["V1", "V2", "V3"]
    assert len(proposals.list_proposals(store, USER)) == 3


def test_seed_verdicts_cite_only_the_initial_values(store, datasource):
    ids = set(install_initial_values(store, datasource, USER, NOW))
    cited = {value_id for reel in datasource.get_session_reels(USER, "s_002") for value_id in reel["value_ids"]}

    assert cited <= ids


@pytest.fixture
def model_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.delenv(config.API_KEY_VAR, raising=False)
    monkeypatch.delenv(models.REQUESTS_PER_MINUTE_VAR, raising=False)
    models.shared_rate_limiter.cache_clear()
    yield monkeypatch
    models.shared_rate_limiter.cache_clear()


def test_build_model_requires_an_api_key(model_env):
    with pytest.raises(config.ConfigError):
        models.build_model(config.DEFAULT_CHAT_MODEL)


def test_build_model_creates_the_named_model_without_calling_the_api(model_env):
    model_env.setenv(config.API_KEY_VAR, "test-key-not-real")

    model = models.build_model(config.DEFAULT_CHAT_MODEL)

    assert "gemini-3.5-flash-lite" in model.model
    assert model.rate_limiter is models.shared_rate_limiter()


def test_requests_per_minute_reads_the_environment(model_env):
    assert models.requests_per_minute() == models.DEFAULT_REQUESTS_PER_MINUTE
    model_env.setenv(models.REQUESTS_PER_MINUTE_VAR, "30")
    assert models.requests_per_minute() == 30.0


@pytest.mark.parametrize("raw", ["fast", "0", "-5"])
def test_requests_per_minute_rejects_invalid_values(model_env, raw: str):
    model_env.setenv(models.REQUESTS_PER_MINUTE_VAR, raw)

    with pytest.raises(config.ConfigError):
        models.requests_per_minute()
