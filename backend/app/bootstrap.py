import importlib
import importlib.util
from dataclasses import dataclass, field

# every module that registers adapters, stages, gates or retention rules at import time;
# modules not written yet are tolerated, errors inside existing modules are not
REGISTRATION_MODULES: tuple[str, ...] = (
    "app.retention.rules",
    "app.pipeline.definitions",
    "app.pipeline.config_source",
    "app.pipeline.stages.orchestration",
    "app.pipeline.stages.finalize",
    "app.workers.queue",
    "app.adapters.youtube.factory",
    "app.adapters.llm.factory",
    "app.adapters.search.tavily",
    "app.services.competitors.site_fetcher",
    "app.adapters.embeddings",
    "app.adapters.freshness.arxiv",
    "app.adapters.freshness.gdelt",
    "app.adapters.freshness.huggingface",
    "app.adapters.freshness.openfda",
    "app.adapters.freshness.pubmed",
    "app.adapters.freshness.rss",
    "app.adapters.freshness.tavily",
    "app.adapters.freshness.who_don",
    "app.services.analytics.thumbnails.fetch",
    "app.services.analytics.thumbnails.rapidocr_reader",
    "app.services.analytics.traits.description",
    "app.services.analytics.traits.duration",
    "app.services.analytics.traits.thumbnail",
    "app.services.analytics.traits.timing",
    "app.services.analytics.traits.title",
    "app.services.analytics.traits.topic",
    "app.services.news.gates",
    "app.services.recommend.gates.base",
    "app.services.recommend.recommenders.base",
    "app.services.refresh.gates",
    "app.pipeline.stages.youtube",
    "app.pipeline.stages.website_intel",
    "app.pipeline.stages.niche",
    "app.pipeline.stages.topic_seed",
    "app.pipeline.setup_confirmer",
    "app.pipeline.maintenance",
    "app.pipeline.stages.competitors",
    "app.pipeline.stages.topics",
    "app.pipeline.stages.analytics",
    "app.pipeline.stages.recommend",
    "app.pipeline.stages.news",
    "app.pipeline.stages.refresh",
    "app.pipeline.stages.cards",
    "app.pipeline.stages.evaluation",
    "app.evaluation.registrations",
    "app.api.services_impl",
)


@dataclass
class BootstrapReport:
    loaded: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def load_registrations(modules: tuple[str, ...] = REGISTRATION_MODULES) -> BootstrapReport:
    report = BootstrapReport()
    for name in modules:
        if not _exists(name):
            report.missing.append(name)
            continue
        importlib.import_module(name)
        report.loaded.append(name)
    return report
