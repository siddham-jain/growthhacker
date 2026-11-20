import os
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

Tier = Literal["low", "medium", "high"]
Effort = Literal["low", "medium", "high"]
TIERS: tuple[Tier, ...] = ("low", "medium", "high")

# provider secrets and infra urls keep their established env names (99 C87)
LEGACY_ENV_PATHS: dict[str, tuple[str, ...]] = {
    "DATABASE_URL": ("db", "url"),
    "REDIS_URL": ("redis", "url"),
    "YOUTUBE_API_KEY": ("youtube", "api_key"),
    "ANTHROPIC_API_KEY": ("llm", "anthropic", "api_key"),
    "ANTHROPIC_BASE_URL": ("llm", "anthropic", "base_url"),
    "LLM_ENABLED": ("llm", "enabled"),
    "LLM_FALLBACK_BASE_URL": ("llm", "openai_compat", "base_url"),
    "LLM_FALLBACK_API_KEY": ("llm", "openai_compat", "api_key"),
    "LLM_FALLBACK_MODEL_LOW": ("llm", "openai_compat", "low", "model"),
    "LLM_FALLBACK_MODEL_MEDIUM": ("llm", "openai_compat", "medium", "model"),
    "LLM_FALLBACK_MODEL_HIGH": ("llm", "openai_compat", "high", "model"),
    "TAVILY_API_KEY": ("tavily", "api_key"),
    "GOOGLE_OAUTH_CLIENT_ID": ("google_oauth", "client_id"),
    "GOOGLE_OAUTH_CLIENT_SECRET": ("google_oauth", "client_secret"),
    "GOOGLE_OAUTH_REDIRECT_URI": ("google_oauth", "redirect_uri"),
    "NCBI_API_KEY": ("freshness", "ncbi_api_key"),
    "OPENFDA_API_KEY": ("freshness", "openfda_api_key"),
    "EVAL_BUNDLE_DIR": ("evaluation", "bundle_dir"),
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class AppSettings(BaseModel):
    env: Literal["local", "test", "staging", "prod"] = "local"
    base_url: str = "http://localhost:8000"
    cors_origins: list[str] = ["http://localhost:3000"]
    log_level: str = "INFO"
    log_json: bool = False


class DatabaseSettings(BaseModel):
    url: SecretStr = SecretStr("postgresql+asyncpg://growth:growth@localhost:5432/growthhacker")
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout_s: float = 10
    statement_timeout_ms: int = 30_000
    idle_in_tx_timeout_ms: int = 60_000
    lock_timeout_ms: int = 5_000
    application_name: str = "gh-api"
    use_external_pooler: bool = False
    echo: bool = False


class RedisSettings(BaseModel):
    url: SecretStr = SecretStr("redis://localhost:6379/0")
    key_prefix: str = "gh"
    lock_default_ttl_s: float = 60
    lock_poll_interval_s: float = 0.25
    lock_max_wait_s: float = 60


class YouTubeSettings(BaseModel):
    api_key: SecretStr | None = None
    base_url: str = "https://www.googleapis.com/youtube/v3"
    request_timeout_s: float = 15


class GoogleOAuthSettings(BaseModel):
    client_id: str | None = None
    client_secret: SecretStr | None = None
    redirect_uri: str = "http://localhost:8000/api/v1/integrations/youtube/callback"
    scopes: list[str] = [
        "https://www.googleapis.com/auth/yt-analytics.readonly",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    reverify_interval_days: int = 25
    risc_enabled: bool = False


class LLMTierSettings(BaseModel):
    model: str | None = None
    max_tokens: int = 4096
    timeout_s: float = 30
    max_retries: int = 2
    effort: Effort | None = None


class LLMProviderSettings(BaseModel):
    defaults: ClassVar[dict[str, Any]] = {}

    kind: Literal["anthropic", "openai_compatible"]
    base_url: str | None = None
    api_key: SecretStr | None = None
    max_concurrency: int = 4
    connect_timeout_s: float = 5
    allowed_data_classes: list[str] = []
    low: LLMTierSettings
    medium: LLMTierSettings
    high: LLMTierSettings

    @model_validator(mode="before")
    @classmethod
    def _apply_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return deep_merge(cls.defaults, data)
        return data

    @property
    def configured(self) -> bool:
        return self.api_key is not None and (self.kind == "anthropic" or self.base_url is not None)

    def tier(self, name: Tier) -> LLMTierSettings:
        return getattr(self, name)


class AnthropicSettings(LLMProviderSettings):
    defaults: ClassVar[dict[str, Any]] = {
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com",
        "max_concurrency": 8,
        "allowed_data_classes": [
            "public_youtube",
            "public_web",
            "news",
            "client_profile",
            "comments_redacted",
            "client_private_analytics",
        ],
        "low": {"model": "claude-haiku-4-5", "max_tokens": 2048, "timeout_s": 20},
        "medium": {"model": "claude-sonnet-5-5", "effort": "low", "max_tokens": 4096, "timeout_s": 35},
        "high": {"model": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 8192, "timeout_s": 60},
    }


class OpenAICompatSettings(LLMProviderSettings):
    defaults: ClassVar[dict[str, Any]] = {
        "kind": "openai_compatible",
        "max_concurrency": 4,
        "allowed_data_classes": ["public_youtube", "public_web", "news", "client_profile"],
        "low": {"timeout_s": 25},
        "medium": {"timeout_s": 35},
        "high": {"max_tokens": 8192, "timeout_s": 60},
    }


class LLMBudgetSettings(BaseModel):
    per_call_max_usd: float = 0.10
    onboarding_run_max_usd: float = 1.50
    analysis_run_max_usd: float = 1.50
    daily_refresh_run_max_usd: float = 0.75
    client_daily_max_usd: float = 3.00
    global_daily_max_usd: float = 50.00


class LLMPrice(BaseModel):
    input: float
    output: float
    cache_write_5m: float
    cache_read: float


class LLMRoute(BaseModel):
    provider_name: str
    tier: LLMTierSettings


class LLMSettings(BaseModel):
    enabled: bool = True
    anthropic: AnthropicSettings = Field(default_factory=lambda: AnthropicSettings())
    openai_compat: OpenAICompatSettings = Field(default_factory=lambda: OpenAICompatSettings())
    provider_order: list[Literal["anthropic", "openai_compat"]] = ["anthropic", "openai_compat"]
    budgets: LLMBudgetSettings = LLMBudgetSettings()
    prices: dict[str, LLMPrice] = {
        "claude-haiku-4-5": LLMPrice(input=1.00, output=5.00, cache_write_5m=1.25, cache_read=0.10),
        "claude-sonnet-5-5": LLMPrice(input=2.00, output=10.00, cache_write_5m=2.50, cache_read=0.10),
    }
    result_cache_default_ttl_days: int = Field(default=7, ge=1, le=28)
    debug_payload_logging: bool = False

    def provider(self, name: str) -> LLMProviderSettings:
        return getattr(self, name)

    def routes(self, tier: Tier) -> list[LLMRoute]:
        routes = []
        for name in self.provider_order:
            provider = self.provider(name)
            tier_settings = provider.tier(tier)
            if provider.configured and tier_settings.model:
                routes.append(LLMRoute(provider_name=name, tier=tier_settings))
        return routes


class TavilySettings(BaseModel):
    api_key: SecretStr | None = None
    request_timeout_s: float = 30
    onboarding_max_credits: int = 60
    news_daily_max_credits: int = 300


class CrawlerSettings(BaseModel):
    user_agent: str = "GrowthHackerBot/0.1 (+https://growthhacker.local/bot)"
    compat_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36 GrowthHackerBot/0.1"
    )
    max_pages: int = 60
    max_renders: int = 12
    max_page_chars: int = 60_000
    request_timeout_s: float = 20
    per_host_concurrency: int = 2
    max_tavily_credits: int = 30
    max_reader_fetches: int = 20
    max_archive_fetches: int = 20
    crawled_pages_ttl_days: int = 180


class SearchBudgetSplit(BaseModel):
    onboarding: int = 30
    news: int = 15
    niche: int = 55


class QuotaSettings(BaseModel):
    project: str = "default"
    timezone: str = "America/Los_Angeles"
    main_daily_units: int = 10_000
    search_daily_calls: int = 100
    search_split: SearchBudgetSplit = SearchBudgetSplit()
    batch_stats_daily_units: int = 10_000
    analytics_daily_requests: int | None = None
    compliance_reserve_pct: int = Field(default=10, ge=0, le=50)
    onboarding_run_units: int = 400
    onboarding_run_search_calls: int = 15
    daily_refresh_run_units: int = 80
    daily_refresh_run_search_calls: int = 5
    competitor_resolution_search_calls: int = 3
    counter_ttl_s: int = 48 * 3600

    @model_validator(mode="after")
    def _split_fits_bucket(self) -> "QuotaSettings":
        split = self.search_split
        if split.onboarding + split.news + split.niche > self.search_daily_calls:
            raise ValueError("quota.search_split exceeds quota.search_daily_calls")
        return self


class PolicySettings(BaseModel):
    retention_mode: Literal["strict_30d", "derived_metrics_36m"] = "strict_30d"
    max_age_days: int = 30
    retention_days: int = 28
    derived_metrics_months: int = 36

    @model_validator(mode="after")
    def _act_before_deadline(self) -> "PolicySettings":
        if not 0 < self.retention_days < self.max_age_days:
            raise ValueError("policy.retention_days must be positive and below policy.max_age_days")
        return self


class RetentionSettings(BaseModel):
    snapshot_partition_days_ahead: int = 7
    refresh_lookahead_hours: int = 6
    refresh_batch_size: int = 50
    delete_batch_size: int = 1000
    run_events_ttl_days: int = 28
    run_artifacts_ttl_days: int = 90
    pipeline_runs_ttl_days: int = 180
    news_ttl_days: int = 90
    tenant_signal_ttl_days: int = 180
    ledger_ttl_days: int = 400
    revoked_key_ttl_days: int = 90
    channel_resolution_ttl_days: int = 7
    link_check_ttl_days: int = 7


class AnalyticsSettings(BaseModel):
    windows: list[int] = [7, 30, 90]
    default_window_days: int = 30
    comparison_age_days: dict[int, int] = {7: 3, 30: 7, 90: 7}
    baseline_horizon_days: int = 180
    outlier_ratio_min: float = 3.0
    outlier_view_floor: int = 100
    min_reference_videos: int = 3
    high_confidence_outliers: int = 5
    high_confidence_channels: int = 3
    lift_min_outliers: int = 3
    lift_min_channels: int = 2
    lift_fdr_q: float = 0.10
    proxy_switch_coverage: float = 0.80


class SecurityKey(BaseModel):
    kid: str
    key: SecretStr


class SecuritySettings(BaseModel):
    token_encryption_keys: list[SecurityKey] = []
    hash_pepper: SecretStr | None = None
    session_ttl_h: int = 336
    session_idle_ttl_h: int = 72
    api_key_prefix: str = "ghk"


class WorkerSettings(BaseModel):
    interactive_queue: str = "gh:interactive"
    batch_queue: str = "gh:batch"
    maintenance_queue: str = "gh:maintenance"
    job_timeout_s: int = 900
    max_jobs: int = 10


class FeatureFlags(BaseModel):
    rls_enabled: bool = False
    news_radar: bool = True
    description_refresh: bool = True
    oauth_analytics: bool = True


class FreshnessSettings(BaseModel):
    ncbi_api_key: SecretStr | None = None
    openfda_api_key: SecretStr | None = None


class EvaluationSettings(BaseModel):
    bundle_dir: Path = REPO_ROOT / "var" / "eval_bundles"


class AdapterSettings(BaseModel):
    # new ports are configured without code changes, e.g. GH_ADAPTERS__RETENTION_REFRESHER=youtube_refresh
    model_config = ConfigDict(extra="allow")

    youtube: str = "youtube_data_api"
    llm: str = "fallback_chain"
    web_search: str = "tavily"
    site_crawler: str = "polite_crawler"
    embedder: str = "fastembed"
    job_queue: str = "arq"
    progress_reporter: str = "run_events"


class LegacyEnvSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], env_file: Path | None) -> None:
        super().__init__(settings_cls)
        self.env_file = env_file

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        raw: dict[str, str | None] = {}
        if self.env_file is not None and self.env_file.is_file():
            raw.update(dotenv_values(self.env_file))
        raw.update({name: os.environ[name] for name in LEGACY_ENV_PATHS if name in os.environ})
        nested: dict[str, Any] = {}
        for name, path in LEGACY_ENV_PATHS.items():
            value = raw.get(name)
            if not value:
                continue
            cursor = nested
            for key in path[:-1]:
                cursor = cursor.setdefault(key, {})
            cursor[path[-1]] = value
        return nested


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GH_",
        env_nested_delimiter="__",
        env_file=DEFAULT_ENV_FILE,
        env_ignore_empty=True,
        extra="ignore",
        secrets_dir=None,
    )

    app: AppSettings = AppSettings()
    db: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    youtube: YouTubeSettings = YouTubeSettings()
    google_oauth: GoogleOAuthSettings = GoogleOAuthSettings()
    llm: LLMSettings = Field(default_factory=LLMSettings)
    tavily: TavilySettings = TavilySettings()
    crawler: CrawlerSettings = CrawlerSettings()
    quota: QuotaSettings = QuotaSettings()
    policy: PolicySettings = PolicySettings()
    retention: RetentionSettings = RetentionSettings()
    analytics: AnalyticsSettings = AnalyticsSettings()
    security: SecuritySettings = SecuritySettings()
    workers: WorkerSettings = WorkerSettings()
    features: FeatureFlags = FeatureFlags()
    freshness: FreshnessSettings = FreshnessSettings()
    evaluation: EvaluationSettings = EvaluationSettings()
    adapters: AdapterSettings = AdapterSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        env_file = settings_cls.model_config.get("env_file")
        legacy = LegacyEnvSource(settings_cls, Path(env_file) if env_file else None)
        return init_settings, env_settings, legacy, dotenv_settings, file_secret_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
