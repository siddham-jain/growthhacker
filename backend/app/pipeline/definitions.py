from types import MappingProxyType

from app.domain.runs import RunKind
from app.pipeline.registry import PipelineDefinition, register_pipeline

ONBOARDING_STAGES = (
    "resolve_inputs",
    "crawl_site",
    "prefetch_own_channel",
    "build_profile",
    "discover_competitors",
    "seed_topic_refs",
    "finish_onboarding",
)
ANALYSIS_STAGES = (
    "snapshot_config",
    "ingest",
    "niche_discovery",
    "own_analytics",
    "news",
    "competitor_drift",
    "enrich",
    "topics",
    "analytics",
    "recommend",
    "write_cards",
    "refresh",
    "finalize",
)
ANALYSIS_INPUTS = ("ingest", "niche_discovery", "own_analytics", "enrich", "topics", "analytics")

ONBOARDING = register_pipeline(
    PipelineDefinition(
        name="onboarding", kind=RunKind.onboarding, stages=ONBOARDING_STAGES, queue="interactive", priority=10
    )
)
COMPETITOR_SUGGEST = register_pipeline(
    PipelineDefinition(
        name="competitor_suggest",
        kind=RunKind.onboarding,
        stages=("discover_competitors",),
        inherited=(
            "resolve_inputs",
            "crawl_site",
            "prefetch_own_channel",
            "build_profile",
            "seed_topic_refs",
        ),
        priority=9,
        drives_lifecycle=False,
    )
)
ANALYSIS = register_pipeline(
    PipelineDefinition(name="analysis", kind=RunKind.analysis, stages=ANALYSIS_STAGES, priority=8)
)
DAILY_REFRESH = register_pipeline(
    PipelineDefinition(
        name="daily_refresh",
        kind=RunKind.daily_refresh,
        stages=ANALYSIS_STAGES,
        queue="batch",
        priority=3,
    )
)
CARDS = register_pipeline(
    PipelineDefinition(
        name="cards",
        kind=RunKind.cards,
        stages=("snapshot_config", "write_cards", "finalize"),
        inherited=(*ANALYSIS_INPUTS, "news", "recommend", "refresh"),
        priority=9,
    )
)
NEWS_SCAN = register_pipeline(
    PipelineDefinition(
        name="news_scan",
        kind=RunKind.news_scan,
        stages=("snapshot_config", "news", "recommend", "write_cards", "finalize"),
        inherited=(*ANALYSIS_INPUTS, "refresh"),
        priority=5,
    )
)
RECRAWL = register_pipeline(
    PipelineDefinition(
        name="recrawl",
        kind=RunKind.recrawl,
        stages=("crawl_site", "build_profile"),
        inherited=("resolve_inputs", "prefetch_own_channel"),
        queue="batch",
        priority=1,
        drives_lifecycle=False,
    )
)
BACKTEST = register_pipeline(
    PipelineDefinition(
        name="backtest",
        kind=RunKind.backtest,
        stages=(
            "snapshot_config",
            "frozen_corpus",
            "enrich",
            "topics",
            "analytics",
            "recommend",
            "write_cards",
            "record_backtest",
        ),
        substitutes=MappingProxyType({"ingest": "frozen_corpus", "finalize": "record_backtest"}),
        queue="batch",
        priority=0,
        drives_lifecycle=False,
    )
)


def default_pipeline(kind: RunKind) -> str:
    return kind.value
