import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import LegacyEnvSource, QuotaSettings, Settings
from tests.conftest import IsolatedSettings, make_settings


def test_defaults_follow_owner_decisions() -> None:
    settings = IsolatedSettings()
    anthropic = settings.llm.anthropic
    assert anthropic.low.model == "claude-haiku-4-5"
    assert anthropic.low.effort is None
    assert (anthropic.medium.model, anthropic.medium.effort) == ("claude-sonnet-5-5", "low")
    assert (anthropic.high.model, anthropic.high.effort) == ("claude-sonnet-5-5", "medium")
    assert settings.quota.search_split.model_dump() == {"onboarding": 30, "news": 15, "niche": 15, "trends": 40}
    assert settings.quota.main_daily_units == 10_000
    assert settings.quota.batch_stats_daily_units == 10_000
    assert settings.policy.retention_mode == "strict_30d"
    assert settings.policy.retention_days == 28
    assert settings.analytics.outlier_ratio_min == 3.0


def test_nested_env_override_keeps_tier_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_LLM__ANTHROPIC__MEDIUM__TIMEOUT_S", "12")
    monkeypatch.setenv("GH_QUOTA__SEARCH_SPLIT__NICHE", "10")
    settings = IsolatedSettings()
    assert settings.llm.anthropic.medium.timeout_s == 12
    assert settings.llm.anthropic.medium.model == "claude-sonnet-5-5"
    assert settings.llm.anthropic.medium.effort == "low"
    assert settings.quota.search_split.niche == 10


def test_legacy_env_names_map_into_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "http://fallback.local/v1")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "fallback-key")
    monkeypatch.setenv("LLM_FALLBACK_MODEL_MEDIUM", "fallback-medium")
    monkeypatch.setenv("LLM_ENABLED", "false")
    settings = IsolatedSettings()
    assert settings.youtube.api_key.get_secret_value() == "yt-test-key"
    assert settings.db.url.get_secret_value().endswith("@db:5432/x")
    assert settings.llm.enabled is False
    assert settings.llm.openai_compat.medium.model == "fallback-medium"
    assert settings.llm.openai_compat.medium.timeout_s == 35


def test_prefixed_env_beats_legacy_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "legacy")
    monkeypatch.setenv("GH_YOUTUBE__API_KEY", "prefixed")
    assert IsolatedSettings().youtube.api_key.get_secret_value() == "prefixed"


def test_empty_legacy_values_are_ignored(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("YOUTUBE_API_KEY=\nTAVILY_API_KEY=t-key\n")
    values = LegacyEnvSource(Settings, env_file)()
    assert "youtube" not in values
    assert values["tavily"]["api_key"] == "t-key"


def test_routes_skip_unconfigured_fallback() -> None:
    settings = make_settings(llm={"anthropic": {"api_key": "a"}, "openai_compat": {"low": {"model": "m"}}})
    assert [route.provider_name for route in settings.llm.routes("low")] == ["anthropic"]
    configured = make_settings(
        llm={
            "anthropic": {"api_key": "a"},
            "openai_compat": {"api_key": "b", "base_url": "http://x", "low": {"model": "m"}},
        }
    )
    assert [route.tier.model for route in configured.llm.routes("low")] == ["claude-haiku-4-5", "m"]
    assert [route.provider_name for route in configured.llm.routes("high")] == ["anthropic"]


def test_search_split_cannot_exceed_bucket() -> None:
    with pytest.raises(ValidationError, match="search_split"):
        QuotaSettings(search_daily_calls=100, search_split={"onboarding": 60, "news": 15, "niche": 55})


def test_retention_days_must_precede_deadline() -> None:
    with pytest.raises(ValidationError, match="retention_days"):
        make_settings(policy={"retention_days": 30})


def test_secrets_never_render() -> None:
    settings = make_settings(youtube={"api_key": "super-secret"})
    assert isinstance(settings.youtube.api_key, SecretStr)
    assert "super-secret" not in repr(settings)
    assert "super-secret" not in settings.model_dump_json()
