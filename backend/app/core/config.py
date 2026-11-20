import os
from datetime import date
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


class YouTubeRetrySettings(BaseModel):
    max_attempts: int = 4
    base_s: float = 0.5
    cap_s: float = 20
    circuit_failures: int = 5
    circuit_window_s: float = 60
    circuit_open_s: float = 60
    key_circuit_open_s: float = 600
    max_concurrency: int = 8
    requests_per_s: float = 20


class YouTubeFreshnessSettings(BaseModel):
    alias_h: int = 7 * 24
    alias_not_found_h: int = 24
    channel_metadata_h: int = 7 * 24
    channel_stats_h: int = 24
    uploads_own_competitor_h: int = 6
    uploads_niche_h: int = 72
    video_metadata_h: int = 7 * 24
    own_video_metadata_h: int = 24
    comment_sample_h: int = 7 * 24
    search_week_h: int = 12
    search_month_h: int = 24
    search_long_h: int = 72
    search_news_h: int = 6


class YouTubeIngestSettings(BaseModel):
    window_max_days: int = 90
    baseline_extra_days: int = 90
    max_horizon_days: int = 365
    min_per_format: int = 30
    max_videos_channel: int = 300
    max_videos_niche: int = 50
    page_size: int = 50
    older_run_to_stop: int = 10
    known_run_to_stop: int = 5
    incremental_max_pages: int = 2
    very_new_channel_videos: int = 5
    removed_after_h: int = 6
    duplicate_title_days: int = 30
    localization_languages: list[str] = ["en"]


class YouTubeFormatSettings(BaseModel):
    shorts_extended_from: date = date(2024, 10, 15)
    artist_shorts_extended_from: date = date(2025, 12, 8)
    shorts_max_s: int = 180
    legacy_shorts_max_s: int = 60
    embed_max_width: int = 400


class CountingRegime(BaseModel):
    since: date
    label: str


class YouTubeSnapshotSettings(BaseModel):
    milestones_h: dict[int, float] = {24: 0.5, 72: 1.0, 168: 1.0, 720: 3.0}
    fallback_window_h: float = 6
    fallback_milestones_h: list[int] = [24, 72, 168]
    batch_size: int = 50
    max_batches_per_tick: int = 40
    compact_after_days: int = 8
    weekly_after_days: int = 35
    counting_regimes: list[CountingRegime] = [
        CountingRegime(since=date(2025, 3, 31), label="2025-03-31-shorts-replays"),
        CountingRegime(since=date(2026, 8, 27), label="2026-08-27-playback-start"),
        CountingRegime(since=date(2026, 11, 16), label="2026-11-16-coviews"),
    ]


class YouTubeCommentSettings(BaseModel):
    max_results: int = 100
    onboarding_calls: int = 30
    daily_calls: int = 10
    top_per_competitor: int = 5
    max_chars: int = 1500


class YouTubeOwnershipSettings(BaseModel):
    site_links_channel: float = 0.50
    confirmed_site_links_channel: float = 0.30
    embed_majority: float = 0.30
    channel_links_site: float = 0.40
    uploads_link_site: float = 0.20
    uploads_link_share: float = 0.30
    user_asserted: float = 0.30
    site_links_other_channel: float = -0.40
    few_videos_penalty: float = -0.20
    country_mismatch_penalty: float = -0.15
    unofficial_penalty: float = -0.40
    verified_min: float = 0.80
    likely_min: float = 0.50
    uncertain_min: float = 0.20


class YouTubeAnalyticsApiSettings(BaseModel):
    base_url: str = "https://youtubeanalytics.googleapis.com/v2"
    repull_days: int = 3
    backfill_days: int = 90
    top_videos: int = 200
    per_video_detail: int = 10
    search_terms: int = 25


class YouTubeSettings(BaseModel):
    api_key: SecretStr | None = None
    base_url: str = "https://www.googleapis.com/youtube/v3"
    request_timeout_s: float = 15
    retry: YouTubeRetrySettings = YouTubeRetrySettings()
    freshness: YouTubeFreshnessSettings = YouTubeFreshnessSettings()
    ingest: YouTubeIngestSettings = YouTubeIngestSettings()
    formats: YouTubeFormatSettings = YouTubeFormatSettings()
    snapshots: YouTubeSnapshotSettings = YouTubeSnapshotSettings()
    comments: YouTubeCommentSettings = YouTubeCommentSettings()
    ownership: YouTubeOwnershipSettings = YouTubeOwnershipSettings()
    analytics_api: YouTubeAnalyticsApiSettings = YouTubeAnalyticsApiSettings()
    coalesce_window_ms: int = 150
    inflight_ttl_s: int = 30


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
    max_retries: int = 1
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

    structured_dialect: Literal[
        "openai_json_schema_strict", "openai_json_schema", "openai_json_object", "prompt_only"
    ] = "openai_json_schema_strict"
    token_param: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    # tier effort becomes reasoning_effort only for gateways that accept it
    supports_reasoning_effort: bool = False
    extra_headers: dict[str, str] = {}
    extra_body: dict[str, Any] = {}
    # passed verbatim to chat.completions.create, e.g. temperature for a local vllm model
    extra_params: dict[str, Any] = {}
    context_window: int = 128_000
    max_output_tokens: int = 16_384


class LLMTaskSettings(BaseModel):
    # per-task overrides of the routing table; unset fields keep the registered default
    tier: Tier | None = None
    max_output_tokens: int | None = Field(default=None, gt=0)
    timeout_s: float | None = Field(default=None, gt=0)
    max_attempts: int | None = Field(default=None, ge=1)
    cache_ttl_days: int | None = Field(default=None, ge=0, le=28)
    prompt_version: int | None = Field(default=None, ge=1)


class LLMResilienceSettings(BaseModel):
    retry_base_s: float = 0.5
    retry_cap_s: float = 4.0
    rate_limit_max_wait_s: float = 5.0
    min_viable_s: float = 5.0
    context_window_share: float = Field(default=0.8, gt=0, le=1)
    circuit_consecutive_failures: int = 5
    circuit_window_calls: int = 20
    circuit_window_s: float = 60
    circuit_failure_ratio: float = 0.5
    circuit_cooldown_s: float = 30
    circuit_max_cooldown_s: float = 300
    spend_cap_open_s: float = 3600
    per_run_concurrency: int = 6
    aimd_cooldown_s: float = 60
    aimd_recovery_successes: int = 10
    # tiers tried after the tier's own provider routes, e.g. haiku standing in for a medium task
    cross_tier_fallback: dict[Tier, list[Tier]] = {"low": ["medium"], "medium": ["low"], "high": []}


class CardWriterSettings(BaseModel):
    max_attempts: int = Field(default=3, ge=1)
    max_llm_calls_per_card: int = Field(default=6, ge=1)
    deadline_s: float = 45
    min_attempt_s: float = 5
    concurrency: int = Field(default=6, ge=1)
    critic_enabled: bool = True
    critic_batch_size: int = Field(default=5, ge=1, le=5)
    critic_advisory_revise_rate: float = 0.6
    number_rel_tol: float = 0.005
    multiple_word_tol: float = 0.25
    banned_phrases: list[str] = [
        "game-changer",
        "game changer",
        "unlock",
        "skyrocket",
        "viral",
        "guaranteed",
        "delve",
        "leverage",
        "in today's fast-paced",
        "boost engagement",
        "take it to the next level",
        "supercharge",
    ]
    canary_salt: str = "growthhacker"


class LLMBudgetSettings(BaseModel):
    # covers the worst case of 03's profile synthesis (sonnet, 12k output tokens, long fact input)
    per_call_max_usd: float = 0.40
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
    tasks: dict[str, LLMTaskSettings] = {}
    resilience: LLMResilienceSettings = LLMResilienceSettings()
    writer: CardWriterSettings = CardWriterSettings()

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


class CrawlProfileSettings(BaseModel):
    discovery_fetch_budget: int = 25
    fetch_budget: int = 90
    map_page_budget: int = 60
    max_link_depth: int = 3
    max_sitemap_files: int = 20
    max_sitemap_urls: int = 50_000
    per_template_cap: int = 3
    max_concurrency_per_host: int = 2
    min_delay_s: float = 1.0
    render_budget: int = 12
    tavily_max_credits: int = 30
    reader_budget: int = 20
    archive_budget: int = 20
    pdf_budget: int = 5
    phase_a_timeout_s: float = 60
    phase_c_timeout_s: float = 150
    wall_seconds: float = 300
    max_html_bytes: int = 5_000_000
    max_pdf_bytes: int = 10_000_000
    map_page_char_cap: int = 24_000
    stored_text_char_cap: int = 60_000
    reduce_input_token_cap: int = 120_000
    selection_candidates: int = 300


class WebsiteIntelSettings(BaseModel):
    client: CrawlProfileSettings = CrawlProfileSettings()
    competitor: CrawlProfileSettings = CrawlProfileSettings(
        discovery_fetch_budget=10,
        fetch_budget=25,
        map_page_budget=15,
        max_link_depth=2,
        max_sitemap_files=8,
        max_sitemap_urls=20_000,
        per_template_cap=2,
        max_concurrency_per_host=1,
        min_delay_s=2.0,
        render_budget=3,
        tavily_max_credits=10,
        reader_budget=8,
        archive_budget=8,
        pdf_budget=0,
        phase_a_timeout_s=30,
        phase_c_timeout_s=60,
        wall_seconds=120,
        map_page_char_cap=12_000,
        stored_text_char_cap=30_000,
        reduce_input_token_cap=40_000,
        selection_candidates=100,
    )
    robots_token: str = "GrowthHackerBot"
    robots_cache_ttl_s: int = 86_400
    quote_match_threshold: float = 92
    min_quote_chars: int = 12
    archive_stale_after_days: int = 548
    disabled_sources: list[str] = []
    jina_reader_url: str = "https://r.jina.ai/"
    wayback_cdx_url: str = "https://web.archive.org/cdx/search/cdx"
    wayback_raw_url: str = "https://web.archive.org/web/{timestamp}id_/{url}"
    common_crawl_index_url: str = "https://index.commoncrawl.org/collinfo.json"
    common_crawl_data_url: str = "https://data.commoncrawl.org/"
    wikidata_api_url: str = "https://www.wikidata.org/w/api.php"
    wikipedia_summary_url: str = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
    web_mention_queries: int = 3
    search_index_queries: int = 4
    search_cache_ttl_s: int = 86_400
    probe_interval_days: int = 7
    full_recrawl_days: int = 90
    map_concurrency: int = 8


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
    # share of a bucket each priority class may push total usage up to
    class_ceilings: dict[str, dict[str, float]] = {
        "interactive": {"main": 1.0, "search": 1.0, "batch_stats": 1.0, "analytics": 1.0},
        "daily_refresh": {"main": 0.85, "search": 0.70, "batch_stats": 0.95, "analytics": 1.0},
        "snapshot": {"main": 0.90, "search": 0.0, "batch_stats": 0.90, "analytics": 0.0},
        "background": {"main": 0.60, "search": 0.40, "batch_stats": 0.70, "analytics": 1.0},
    }
    client_daily_units: int = 600
    client_daily_search_calls: int = 20
    redis_down_interactive_units: int = 200

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


class RefreshNeedWeights(BaseModel):
    news: float = 0.45
    evergreen: float = 0.35
    buyer_gap: float = 0.20


class RefreshPriorityWeights(BaseModel):
    # news urgency is a tie-breaker only, never part of the composite (99 D-POL-7)
    velocity: float
    relevance: float
    demand: float = 0.0


class RefreshSettings(BaseModel):
    boilerplate_min_share: float = 0.3
    boilerplate_min_descriptions: int = 3
    fit_threshold: float = 0.6
    news_relevance_min: float = 0.5
    periodic_news_relevance_min: float = 0.7
    cadence_bypass_relevance: float = 0.8
    news_lookback_days: int = 30
    news_target: int = 6
    need_weights: RefreshNeedWeights = RefreshNeedWeights()
    hygiene_only_below: float = 0.35
    active_from: float = 0.65
    cadence_days: dict[str, int] = {"none": 30, "hygiene_only": 30, "periodic": 14, "active": 3}
    provisional_share_multiplier: float = 0.5
    snapshot_velocity_min_hours: float = 7 * 24
    evergreen_min_age_days: int = 90
    velocity_floor_abs: float = 5.0
    velocity_floor_rel: float = 0.5
    min_age_days: int = 21
    news_min_age_days: int = 14
    hygiene_min_age_days: int = 2
    broken_link_min_views: int = 100
    cooldown_days: int = 45
    dismiss_snooze_days: int = 30
    max_description_age_days: int = 2
    chapters_min_duration_s: int = 480
    shorts_max_duration_s: int = 180
    stale_year_min_age_days: int = 120
    stale_year_from_day_of_year: int = 15
    min_refs: int = 3
    above_fold_chars: int = 150
    keyword_weights: RefreshPriorityWeights = RefreshPriorityWeights(
        velocity=0.35, relevance=0.30, demand=0.25
    )
    hygiene_weights: RefreshPriorityWeights = RefreshPriorityWeights(velocity=0.60, relevance=0.30)
    keyword_show_min: float = 50.0
    hygiene_show_min: float = 30.0
    shorts_news_weight: float = 0.5
    title_presence_relevance_factor: float = 0.5
    max_refreshes_per_run: int = 10
    max_videos_per_keyword: int = 5
    max_new_keywords: int = 3
    max_new_hashtags: int = 3
    max_hashtags_total: int = 15
    hashtags_ignored_over: int = 60
    keyword_max_occurrences: int = 2
    repeat_jaccard: float = 0.8
    max_repeats_per_30d: int = 3
    byte_margin: int = 100
    near_limit_bytes: int = 4900
    supported_languages: list[str] = ["en"]
    llm_phrasing: bool = True
    llm_max_calls: int = 20
    llm_concurrency: int = 5
    link_timeout_s: float = 5.0
    link_max_redirects: int = 5
    link_domain_interval_s: float = 2.0
    link_concurrency: int = 5
    link_max_probes: int = 200
    link_time_box_s: float = 20.0
    link_recheck_5xx_hours: int = 24
    link_cache_days: int = 7
    news_expiry_days: int = 21
    keyword_expiry_days: int = 30
    hygiene_expiry_days: int = 30
    applied_visible_grace_days: int = 2
    applied_token_overlap: float = 0.8
    impact_window_days: int = 14
    impact_up_ratio: float = 1.2
    impact_down_ratio: float = 0.83
    impact_control_min: int = 5
    impact_control_age_tolerance: float = 0.5
    impact_control_refresh_gap_days: int = 30
    min_own_history: int = 3


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
    interactive_max_jobs: int = 4
    batch_max_jobs: int = 6
    maintenance_max_jobs: int = 4
    max_tries: int = 3
    keep_result_s: int = 3600
    job_completion_wait_s: int = 25
    health_check_interval_s: int = 30
    run_timeout_margin_s: int = 120


class FeatureFlags(BaseModel):
    rls_enabled: bool = False
    news_radar: bool = True
    description_refresh: bool = True
    oauth_analytics: bool = True


class FreshnessSettings(BaseModel):
    ncbi_api_key: SecretStr | None = None
    openfda_api_key: SecretStr | None = None


class NewsEnvelope(BaseModel):
    # operating envelope per freshness level (08 §5.1, 99 C25)
    search_cadence_minutes: int = Field(ge=0)
    feed_cadence_minutes: int = Field(ge=0)
    max_queries_per_client: int = Field(ge=0)
    max_judged_per_run: int = Field(ge=0)
    max_opportunities_per_day: int = Field(ge=0)
    demand_checks_per_client_per_day: int = Field(ge=0)
    recency_days: int = Field(ge=1)
    max_story_age_days: int = Field(ge=1)
    tavily_time_range: Literal["day", "week", "month"] = "week"


class NewsSettings(BaseModel):
    envelopes: dict[str, NewsEnvelope] = {
        "high": NewsEnvelope(
            search_cadence_minutes=360,
            feed_cadence_minutes=120,
            max_queries_per_client=12,
            max_judged_per_run=15,
            max_opportunities_per_day=5,
            demand_checks_per_client_per_day=3,
            recency_days=3,
            max_story_age_days=7,
            tavily_time_range="day",
        ),
        "medium": NewsEnvelope(
            search_cadence_minutes=1440,
            feed_cadence_minutes=360,
            max_queries_per_client=8,
            max_judged_per_run=8,
            max_opportunities_per_day=3,
            demand_checks_per_client_per_day=1,
            recency_days=7,
            max_story_age_days=14,
        ),
        "low": NewsEnvelope(
            search_cadence_minutes=10080,
            feed_cadence_minutes=1440,
            max_queries_per_client=4,
            max_judged_per_run=4,
            max_opportunities_per_day=1,
            demand_checks_per_client_per_day=1,
            recency_days=14,
            max_story_age_days=14,
        ),
        "none": NewsEnvelope(
            search_cadence_minutes=43200,
            feed_cadence_minutes=0,
            max_queries_per_client=2,
            max_judged_per_run=2,
            max_opportunities_per_day=1,
            demand_checks_per_client_per_day=0,
            recency_days=14,
            max_story_age_days=14,
        ),
    }
    long_lived_event_recency_days: int = 30
    query_lock_ttl_s: int = 120
    on_demand_cooldown_minutes: int = 30
    rate_limited_retry_minutes: int = 15
    breaker_failure_threshold: int = 3
    breaker_window_s: int = 600
    breaker_open_s: int = 1800
    max_results_per_query: int = Field(default=10, ge=1, le=20)
    future_date_tolerance_hours: int = 6
    fuzzy_title_min_ratio: float = 88.0
    fuzzy_title_window_hours: int = 72
    embedding_merge_min_cosine: float = 0.86
    developing_window_hours: int = 12
    prefilter_min: float = 0.35
    rejudge_min_new_outlets: int = 3
    judge_concurrency: int = 5
    credibility_min: float = 0.5
    relevance_min_for_demand: float = 0.5
    relevance_min_for_opportunity: float = 0.45
    min_reference_videos: int = 3
    early_signal_min_sources: int = 2
    strict_reference_gate: bool = False
    early_video_lead_hours: int = 12
    max_baseline_channels: int = 10
    own_history_min_videos: int = 3
    own_history_lookback_days: int = 365
    health_review_required: bool = True
    ncbi_tool: str = "growthhacker"
    ncbi_email: str = "ops@growthhacker.local"


class CompetitorBudgetSettings(BaseModel):
    youtube_units: int = 60
    search_calls: int = 9
    tavily_credits: int = 20
    llm_usd: float = 0.30
    wall_seconds: float = 120


class CompetitorSourceCaps(BaseModel):
    llm: int = 30
    web_search: int = 40
    website_hint: int = 20
    niche_search: int = 30
    user: int = 20
    rediscovery_news: int = 10


class OwnershipWeights(BaseModel):
    site_links_channel: float = 0.50
    jsonld_sameas: float = 0.50
    embed_majority: float = 0.30
    channel_links_site: float = 0.40
    uploads_link_site: float = 0.20
    uploads_brand_reference: float = 0.10
    name_similarity_strong: float = 0.15
    name_similarity_weak: float = 0.05
    websearch_youtube_hit: float = 0.10
    user_asserted: float = 0.30
    channel_is_entity: float = 0.50
    penalty_few_videos: float = -0.20
    penalty_foreign_country: float = -0.15
    penalty_unofficial_tokens: float = -0.40
    penalty_late_duplicate: float = -0.20
    verified_min: float = 0.80
    likely_min: float = 0.50
    uncertain_min: float = 0.20
    # a strong name match plus one corroborating signal earns the llm review (never more than likely)
    adjudication_min: float = 0.25


class CompetitorSettings(BaseModel):
    budget: CompetitorBudgetSettings = CompetitorBudgetSettings()
    source_caps: CompetitorSourceCaps = CompetitorSourceCaps()
    ownership: OwnershipWeights = OwnershipWeights()
    max_entities_to_resolve: int = 25
    resolution_reserve_aggregators: int = 2
    max_resolution_searches: int = 3
    max_handle_guesses: int = 3
    max_leads_per_entity: int = 3
    site_extra_paths: list[str] = ["/contact", "/about", "/media"]
    embed_min_votes: int = 2
    embed_majority_share: float = 0.5
    uploads_link_share: float = 0.3
    uploads_sample_size: int = 20
    web_search_max_results: int = 8
    hallucination_guard_searches: int = 10
    merge_name_ratio: float = 92
    niche_queries_for_discovery: int = 3
    niche_lookback_days: int = 90
    niche_min_query_hits: int = 2
    niche_min_total_hits: int = 4
    activity_target_uploads_90d: int = 12
    inactive_after_days: int = 90
    business_tier_high: float = 0.65
    business_tier_medium: float = 0.40
    youtube_tier_high_overlap: float = 0.25
    youtube_tier_high_activity: float = 0.50
    youtube_tier_medium_overlap: float = 0.10
    proposal_size: int = 7
    min_competitors: int = 3
    max_competitors: int = 10
    niche_max: int = 20
    benchmark_max: int = 3
    creator_seed_max: int = 30
    auto_confirm: bool = False
    auto_confirm_min_likely_score: float = 0.70
    rediscovery_interval_days: int = 30
    rediscovery_interval_days_fast: int = 14
    resolution_cache_days: int = 30
    niche_riser_window_days: int = 14
    niche_riser_min_queries: int = 3
    overlap_drop_ratio: float = 0.5
    resolve_concurrency: int = 8
    llm_concurrency: int = 4
    lock_ttl_s: float = 300


RECOMMEND_ASSIGNMENT_TYPES = ("video_idea", "title_pattern", "thumbnail_pattern", "format", "creator")
ASSIGNMENT_MIN_REFERENCE_VIDEOS = 3


class RecommendGatePolicy(BaseModel):
    # per-type thresholds; the assignment types stay at 3+ references unless an admin unlocks it
    min_reference_videos: dict[str, int] = {"default": ASSIGNMENT_MIN_REFERENCE_VIDEOS}
    min_distinct_channels: dict[str, int] = {
        "default": 1,
        "title_pattern": 2,
        "thumbnail_pattern": 2,
        "format": 2,
    }
    allow_below_assignment_minimum: bool = False

    @model_validator(mode="after")
    def _minimums_hold(self) -> "RecommendGatePolicy":
        if "default" not in self.min_reference_videos or "default" not in self.min_distinct_channels:
            raise ValueError("recommend.gates needs a default minimum for references and channels")
        for rec_type, minimum in self.min_reference_videos.items():
            if minimum < 1:
                raise ValueError(f"recommend.gates.min_reference_videos[{rec_type}] must be at least 1")
        if not self.allow_below_assignment_minimum:
            for rec_type in RECOMMEND_ASSIGNMENT_TYPES:
                if self.min_refs(rec_type) < ASSIGNMENT_MIN_REFERENCE_VIDEOS:
                    raise ValueError(f"{rec_type} needs {ASSIGNMENT_MIN_REFERENCE_VIDEOS}+ reference videos")
        return self

    def min_refs(self, rec_type: str) -> int:
        return self.min_reference_videos.get(rec_type, self.min_reference_videos["default"])

    def min_channels(self, rec_type: str) -> int:
        return self.min_distinct_channels.get(rec_type, self.min_distinct_channels["default"])


class RecommendNoCopySettings(BaseModel):
    ratio: float = 85
    token_sort_ratio: float = 88
    length_ratio_min: float = 0.75
    length_ratio_max: float = 1.33
    partial_ratio: float = 95
    partial_min_tokens: int = 6
    own_ratio: float = 80


class RecommendRangeSettings(BaseModel):
    n_min: float = 5
    k_peer: float = 8
    k_own: float = 5
    k_blend: float = 5
    own_min_n: int = 3
    own_only_n: int = 20
    fallback_min_matching: int = 8
    ratio_clip_low: float = 0.1
    ratio_clip_high: float = 20.0
    floor: float = 0.5
    cap: float = 3.0
    cap_size_mismatch: float = 2.0
    min_width: float = 1.15
    size_mismatch_share: float = 0.3
    min_client_baseline_n: int = 3
    provisional_baseline_n: int = 5


class RecommendCreatorSettings(BaseModel):
    lookback_days: int = 90
    max_inactive_days: int = 30
    prefilter: int = 40
    to_gate: int = 10
    uploads_cap: int = 50
    min_match_share: float = 0.2
    strong_match_share: float = 0.5
    min_matching_uploads: int = 3
    min_uploads_for_gaps: int = 4
    exclude_made_for_kids: bool = True
    default_sort: Literal[
        "matching_uploads", "median_outlier_ratio", "median_views_at_age", "last_upload_at", "uploads_90d"
    ] = "matching_uploads"
    sponsor_phrases: list[str] = [
        "sponsored by",
        "in partnership with",
        "paid partnership",
        "#ad",
        "#sponsored",
        "प्रायोजित",
    ]


class RecommendFeedbackSettings(BaseModel):
    dismiss_hide_days: int = 90
    dismiss_penalty: float = 0.5
    dismiss_penalty_days: int = 60
    snooze_days: int = 14
    creator_exclusion_days: int = 180
    staleness_grace_days: int = 14
    staleness_base: float = 0.85
    auto_done_days: int = 30


class RecommendSettings(BaseModel):
    min_ref_age_days: int = 2
    client_lookback_days: int = 365
    topic_match_min: float = 0.6
    broad_topic_share: float = 0.4
    direct_match_min: float = 0.70
    directness_share_min: float = 0.6
    presence_half_life_days: float = 180
    stale_coverage_days: int = 180
    underperforming_ratio: float = 0.8
    min_outliers_for_candidate: int = 2
    format_majority_share: float = 0.5
    hint_share_delta: float = 0.2
    event_share: float = 0.7
    event_span_hours: float = 72
    repeat_guard_days: int = 30
    pending_judgement_days: int = 7
    max_reasons: int = 3
    max_fit_reasons: int = 2
    cards_per_window: dict[int, int] = {7: 5, 30: 8, 90: 10}
    type_caps: dict[str, int] = {
        "video_idea": 4,
        "title_pattern": 2,
        "thumbnail_pattern": 2,
        "format": 2,
        "creator": 3,
        "news_opportunity": 2,
    }
    refresh_surface_cap: int = 5
    hygiene_cap: int = 10
    early_signal_cap: int = 5
    early_signal_min_sources: int = 2
    max_ref_reuse: int = 2
    max_channel_ref_share: float = 0.6
    mmr_lambda: float = 0.7
    min_score: float = 0.35
    lens_weights: dict[str, dict[str, float]] = {
        "both": {"evidence": 0.30, "fit": 0.25, "gap": 0.20, "intent": 0.15, "recency": 0.10},
        "views": {"evidence": 0.40, "fit": 0.20, "gap": 0.15, "intent": 0.05, "recency": 0.20},
        "conversion": {"evidence": 0.20, "fit": 0.30, "gap": 0.20, "intent": 0.25, "recency": 0.05},
    }
    merge_jaccard: float = 0.6
    merge_topic_similarity: float = 0.85
    merge_topic_jaccard: float = 0.4
    news_overlap_jaccard: float = 0.5
    subsume_lift_delta: float = 0.2
    contradiction_large_n: int = 10
    min_cards: int = 3
    min_window_outliers: int = 5
    min_outlier_channels: int = 2
    min_matched_topics: int = 5
    stale_data_hours: int = 48
    degraded_min_candidates: int = 10
    gates: RecommendGatePolicy = RecommendGatePolicy()
    no_copy: RecommendNoCopySettings = RecommendNoCopySettings()
    expected_range: RecommendRangeSettings = RecommendRangeSettings()
    creators: RecommendCreatorSettings = RecommendCreatorSettings()
    feedback: RecommendFeedbackSettings = RecommendFeedbackSettings()
    forbidden_field_terms: list[str] = [
        "revenue",
        "earning",
        "cpm",
        "rpm",
        "monetis",
        "monetiz",
        "demographic",
        "audience_age",
        "audience_gender",
        "audience_geo",
        "category",
    ]
    policy_lexicon: dict[str, str] = {
        "channel_income": r"\b(revenue|earnings?|income|cpm|rpm|ad\s+money|monetis\w*|monetiz\w*)\b",
        "audience_makeup": r"\b(viewers|audience|subscribers)\s+(are|is)\s+(mostly|mainly|largely|primarily)\b",
        "age_range": r"\b(aged?|ages)\s+\d{1,2}\s*(?:-|–|to)\s*\d{1,2}\b",
        "gender_audience": r"\b(male|female|men|women)\s+(viewers|audience)\b",
        "demographics": r"\bdemographics?\b",
        "category_label": r"\b(video|content)\s+category\b",
    }
    healthcare_lexicon: dict[str, str] = {
        "cure_claim": r"\b(cures?|cured)\b",
        "outcome_promise": r"\b(guaranteed\s+(recovery|results?|cure)|100%\s+(safe|success\w*|effective))\b",
        "no_risk_claim": r"\b(no\s+side\s+effects|risk[- ]free|painless)\b",
    }
    certainty_lexicon: dict[str, str] = {
        "guarantee": r"\bguarantee[sd]?\b",
        "will_get": r"\bwill\s+(get|go|reach|hit|earn|double|triple)\b",
        "definitely": r"\bdefinitely\b",
        "proven": r"\bproven\s+to\b",
        "viral": r"\bviral\b",
    }

    def with_overrides(self, overrides: dict[str, Any]) -> "RecommendSettings":
        if not overrides:
            return self
        return RecommendSettings.model_validate(deep_merge(self.model_dump(), overrides))


class BacktestSettings(BaseModel):
    window_days: Literal[7, 30, 90] = 30
    horizon_days: int = Field(default=10, ge=1)
    outcome_age_days: int = Field(default=7, ge=1)
    # 07's per-type card caps (99 C18), so every arm gets as many specs as the client would see
    k_per_type: dict[str, int] = {
        "video_idea": 4,
        "title_pattern": 2,
        "thumbnail_pattern": 2,
        "format": 2,
        "creator": 3,
    }
    random_replicates: int = Field(default=1000, ge=1)
    bootstrap_resamples: int = Field(default=10_000, ge=100)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    include_own_channel: bool = False
    baseline_videos: int = Field(default=30, ge=1)
    baseline_min_videos: int = Field(default=5, ge=1)
    min_universe_videos_client: int = 20
    min_matched_videos_client: int = 5
    min_universe_videos_pooled: int = 100
    min_matched_videos_pooled: int = 30
    min_channels_pooled: int = 10
    few_clusters: int = 5
    max_missing_share: float = Field(default=0.2, ge=0, le=1)
    max_usd: float = 5.0
    max_quota_units: int = 1000


class LabellingSettings(BaseModel):
    sample_size: int = 100
    # ratio bands relative to the outlier threshold: r1 >= 2θ, r2 [θ, 2θ), r3 [0.6θ, θ), r4 < 0.6θ
    ratio_band_allocation: dict[str, int] = {"R1": 15, "R2": 30, "R3": 25, "R4": 30}
    shorts_sample_size: int = 30
    max_per_channel: int = 3
    min_per_cell: int = 2
    pack_batch_size: int = 25
    sweep_min: float = 1.5
    sweep_max: float = 6.0
    sweep_step: float = 0.25
    min_precision: float = 0.80
    kappa_gate: float = 0.6
    max_unsure_share: float = 0.15
    bootstrap_resamples: int = Field(default=10_000, ge=100)


class ReviewSettings(BaseModel):
    sample_size: int = 30
    decoys: int = 6
    min_per_type: int = 3
    target_mean: float = 4.0
    min_criterion_mean: float = 3.5
    would_ship_target: float = 0.7
    decoy_gap_min: float = 0.3
    alpha_min: float = 0.667
    retention_min: float = 0.7
    bootstrap_resamples: int = Field(default=10_000, ge=100)


class OpsSettings(BaseModel):
    tavily_usd_per_credit: float = 0.008
    prices_verified_at: date = date(2026, 11, 20)
    price_stale_days: int = 90
    latency_target_s: float = 120.0
    units_per_client_budget: int = 150
    alert_dedupe_minutes: int = 60
    alert_webhook_url: SecretStr | None = None


class EvaluationSettings(BaseModel):
    bundle_dir: Path = REPO_ROOT / "var" / "eval_bundles"
    docs_dir: Path = REPO_ROOT / "docs" / "evaluation"
    seed: int = 20261119
    backtest: BacktestSettings = BacktestSettings()
    labelling: LabellingSettings = LabellingSettings()
    review: ReviewSettings = ReviewSettings()
    ops: OpsSettings = OpsSettings()


class EmbeddingModelSettings(BaseModel):
    name: str
    revision: str
    dim: int
    query_prefix: str = ""
    passage_prefix: str = ""


class EmbeddingSettings(BaseModel):
    # models are baked into images through cache_dir; runtime downloads are a failure mode
    cache_dir: Path = Path.home() / ".cache" / "growthhacker" / "fastembed"
    local_files_only: bool = False
    threads: int | None = 4
    batch_size: int = 64
    memory_cache_size: int = 20_000
    default_model: str = "minilm"
    models: dict[str, EmbeddingModelSettings] = {
        "minilm": EmbeddingModelSettings(
            name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", revision="faf4aa42", dim=384
        ),
        "potion": EmbeddingModelSettings(name="minishlab/potion-multilingual-128M", revision="main", dim=256),
    }
    # script -> fallback chain of model keys
    routes: dict[str, list[str]] = {
        "Latn": ["minilm"],
        "Deva": ["minilm"],
        "Gujr": ["minilm"],
        "Arab": ["minilm"],
        "Taml": ["potion"],
        "Telu": ["potion"],
        "Beng": ["potion"],
        "Knda": ["potion"],
        "Mlym": ["potion"],
        "Guru": ["potion"],
        "Orya": ["potion"],
    }


class TopicEngineSettings(BaseModel):
    field_weights: dict[str, float] = {
        "title": 1.0,
        "chapters": 0.7,
        "description_head": 0.6,
        "hashtags": 0.5,
        "tags": 0.45,
        "description_rest": 0.3,
    }
    head_chars: int = 400
    desc_max_chars: int = 3000
    slop: int = 2
    fuzzy_ratio: float = 88
    fuzzy_min_len: int = 6
    spec_weak: float = 0.3
    corpus_min_docs: int = 200
    generic_df: float = 0.15
    ambig_df: float = 0.02
    alias_min_cos: float = 0.55
    alias_max_tokens: int = 6
    transliteration_min_ratio: float = 80
    proposed_alias_weight: float = 0.7
    use_unconfirmed_aliases: bool = True
    negation_factor: float = 0.3
    negation_window: int = 3
    negative_context_factor: float = 0.2
    subsume_factor: float = 0.5
    max_hits_per_field: int = 2
    tag_stuffed_factor: float = 0.5
    unprofiled_description_factor: float = 0.5
    list_mention_min_refs: int = 4
    list_mention_factor: float = 0.2
    bp_sample: int = 30
    bp_min_videos: int = 5
    bp_line_df: float = 0.4
    bp_hashtag_df: float = 0.4
    bp_tag_df: float = 0.6
    bp_block_share: float = 0.7
    bp_refresh_new_uploads: int = 5
    sem_top_k: int = 5
    sem_scale: dict[str, float] = {"minilm": 0.5, "e5_small": 0.12, "potion": 0.5}
    # platt (a, b) per model key, fitted from labels; absent means linear scaling by sem_scale
    sem_platt: dict[str, tuple[float, float]] = {}
    sem_strong_margin: float = 0.35
    sem_ambiguous_margin: float = 0.15
    lexical_weight: float = 0.9
    semantic_weight: float = 0.75
    combiner_version: str = "noisy_or_v1"
    # logistic combiner [b0..b6] over (L, S, L*S, title_hit, tags_only, quality_low); none keeps noisy-or
    logistic_coefficients: list[float] | None = None
    accept: float = 0.70
    reject: float = 0.35
    high: float = 0.85
    high_min_lexical: float = 0.6
    high_min_semantic: float = 0.5
    semantic_only_forced_below: float = 0.9
    conflict_min_lexical: float = 0.6
    no_embedder_min_lexical: float = 0.8
    adj_batch_videos: int = 20
    adj_max_candidates: int = 5
    adj_concurrency: int = 4
    adj_max_videos_per_run: dict[str, int] = {"cold": 800, "daily": 60}
    adj_description_chars: int = 600
    adj_max_tags: int = 15
    adj_quote_min_partial: float = 95
    # observed on apollo: ~$0.008 per 20-video haiku batch; the estimate keeps a margin over that
    adj_estimated_usd_per_batch: float = 0.012
    max_topics_per_video: int = 5
    lift_min_confidence: Literal["high", "medium", "low"] = "medium"
    lift_fractional_multi: bool = True
    tiebreak_band: float = 0.05
    max_buyer_questions: int = 500
    max_refs_keyword_offering: int = 300
    min_refs: int = 5
    min_buyer_questions: int = 3
    bq_merge_cos: float = 0.92
    parent_link_cos: float = 0.75
    proposer_batch_refs: int = 30
    proposer_max_aliases: int = 8
    default_languages: list[str] = ["en", "hi", "hi-Latn"]
    qa_sample_pct: float = 2.0
    scope_window_days: int = 90
    keyword_suggestions_enabled: bool = False


class AdapterSettings(BaseModel):
    # new ports are configured without code changes, e.g. GH_ADAPTERS__RETENTION_REFRESHER=youtube_refresh
    model_config = ConfigDict(extra="allow")

    youtube: str = "youtube_data_api"
    youtube_analytics: str = "youtube_analytics_v2"
    youtube_data_layer: str = "default"
    llm: str = "fallback_chain"
    web_search: str = "tavily"
    site_crawler: str = "polite_crawler"
    embedder: str = "fastembed"
    job_queue: str = "arq"
    progress_reporter: str = "run_events"
    analytics_inputs: str = "shared_cache"
    image_source: str = "ytimg_http"
    thumbnail_text_reader: str = "rapidocr"
    run_config_source: str = "tables"
    setup_confirmer: str = "tables"


class RunBudgetSettings(BaseModel):
    wall_s: float = Field(gt=0)
    youtube_units: int = Field(ge=0)
    search_calls: int = Field(ge=0)
    llm_usd: float = Field(ge=0)
    tavily_credits: float = Field(ge=0)


def _default_run_budgets() -> dict[str, RunBudgetSettings]:
    rows = {
        "onboarding": (600, 150, 6, 0.60, 40),
        "analysis": (1200, 900, 4, 1.00, 20),
        "daily_refresh": (600, 120, 1, 0.30, 10),
        "cards": (300, 0, 0, 0.40, 0),
        "news_scan": (300, 30, 1, 0.20, 20),
        "recrawl": (900, 0, 0, 0.60, 40),
        "backtest": (1800, 0, 0, 1.00, 0),
    }
    return {
        kind: RunBudgetSettings(
            wall_s=wall, youtube_units=units, search_calls=search, llm_usd=usd, tavily_credits=credits
        )
        for kind, (wall, units, search, usd, credits) in rows.items()
    }


class PipelineSettings(BaseModel):
    max_parallel_stages: int = Field(default=3, ge=1)
    lease_s: float = 45
    heartbeat_s: float = 15
    heartbeat_max_failures: int = 2
    cancel_poll_s: float = 2
    cancel_grace_s: float = 30
    reconcile_lease_grace_s: float = 15
    requeue_after_s: float = 60
    max_attempts: int = 3
    reuse_max_age_h: float = 24
    reuse_max_age_overrides_h: dict[str, float] = {"crawl_site": 168, "build_profile": 168}
    resume_max_age_h: float = 24
    # stages doing i/o get a time bucket in their fingerprint; pure stages hash upstream only
    fingerprint_bucket_h: dict[str, float] = {
        "resolve_inputs": 24,
        "crawl_site": 168,
        "prefetch_own_channel": 6,
        "ingest": 6,
        "niche_discovery": 6,
        "own_analytics": 24,
    }
    disabled_stages: list[str] = []
    require_complete: bool = False
    max_active_runs_per_workspace: int = 3
    max_active_runs_per_workspace_user: int = 5
    batch_max_depth: int = 50
    interactive_reserve_units: int = 1500
    overdue_alert_h: float = 20
    refresh_jitter_minutes: int = 40
    stale_after_h: float = 36
    event_flush_interval_s: float = 0.2
    event_flush_batch: int = 50
    event_buffer_max: int = 2000
    events_per_run_max: int = 5000
    insights_per_stage_max: int = 30
    progress_min_interval_s: float = 0.25
    budgets: dict[str, RunBudgetSettings] = Field(default_factory=_default_run_budgets)

    def budget(self, kind: str) -> RunBudgetSettings:
        try:
            return self.budgets[kind]
        except KeyError:
            raise KeyError(f"pipeline.budgets has no entry for run kind '{kind}'") from None


class ApiRateLimitSettings(BaseModel):
    read_per_min: int = 600
    write_per_min: int = 120
    run_start_per_hour_client: int = 20
    run_start_per_hour_workspace: int = 100
    sse_open_per_min: int = 60
    oauth_per_min: int = 10
    local_fallback_share: float = Field(default=0.5, gt=0, le=1)


class ApiSettings(BaseModel):
    # signs cursors and stream tokens; derived from security.hash_pepper when unset
    signing_key: SecretStr | None = None
    session_cookie: str = "gh_session"
    session_cookie_secure: bool = True
    docs_enabled: bool = True
    frontend_url: str = "http://localhost:3000"
    max_body_bytes: int = 1024 * 1024
    idempotency_ttl_h: int = 24
    idempotency_max_body_bytes: int = 64 * 1024
    stream_token_ttl_s: int = 300
    sse_max_connections: int = 1000
    sse_ping_s: float = 15
    sse_poll_s: float = 5
    sse_send_timeout_s: float = 30
    page_default: int = 25
    page_max: int = 100
    interactive_backlog_depth: int = 20
    oauth_state_ttl_s: int = 600
    rate_limits: ApiRateLimitSettings = ApiRateLimitSettings()


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
    website_intel: WebsiteIntelSettings = WebsiteIntelSettings()
    quota: QuotaSettings = QuotaSettings()
    policy: PolicySettings = PolicySettings()
    retention: RetentionSettings = RetentionSettings()
    analytics: AnalyticsSettings = AnalyticsSettings()
    refresh: RefreshSettings = RefreshSettings()
    security: SecuritySettings = SecuritySettings()
    workers: WorkerSettings = WorkerSettings()
    features: FeatureFlags = FeatureFlags()
    freshness: FreshnessSettings = FreshnessSettings()
    news: NewsSettings = NewsSettings()
    competitors: CompetitorSettings = CompetitorSettings()
    recommend: RecommendSettings = RecommendSettings()
    evaluation: EvaluationSettings = EvaluationSettings()
    embeddings: EmbeddingSettings = EmbeddingSettings()
    topics: TopicEngineSettings = TopicEngineSettings()
    adapters: AdapterSettings = AdapterSettings()
    pipeline: PipelineSettings = PipelineSettings()
    api: ApiSettings = ApiSettings()

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
