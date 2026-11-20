from dataclasses import dataclass, replace

from app.core.config import LLMSettings, Tier
from app.core.registry import Registry
from app.ports.llm import TaskId


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """one row of the routing table; every caller of a task owns a deterministic degrade path."""

    task: TaskId
    tier: Tier
    max_output_tokens: int
    timeout_s: float
    expected_reply_tokens: int
    cache_ttl_days: int = 7
    max_attempts: int = 4
    degrade_path: str = ""


llm_task_registry: Registry[TaskSpec] = Registry("llm_task_registry")

DEFAULT_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(TaskId.website_page_selection, "low", 1024, 20, 400, degrade_path="deterministic url scoring"),
    TaskSpec(TaskId.website_page_map, "low", 2048, 25, 800, degrade_path="heading and meta extraction"),
    TaskSpec(
        TaskId.website_profile_reduce,
        "high",
        16_384,
        90,
        4000,
        degrade_path="minimal profile from meta tags, awaiting confirmation",
    ),
    TaskSpec(TaskId.website_keyword_enrichment, "low", 1024, 20, 400, degrade_path="profile keywords only"),
    TaskSpec(TaskId.website_freshness_profile, "low", 1024, 20, 400, degrade_path="vertical default profile"),
    TaskSpec(
        TaskId.competitor_candidate_generation,
        "medium",
        4096,
        35,
        1500,
        degrade_path="search-only candidates",
    ),
    TaskSpec(
        TaskId.competitor_relation_classification,
        "medium",
        4096,
        35,
        1500,
        degrade_path="deterministic candidate score, shown unverified",
    ),
    TaskSpec(
        TaskId.competitor_entity_extraction, "low", 2048, 20, 600, degrade_path="regex entity extraction"
    ),
    TaskSpec(
        TaskId.competitor_ownership_adjudication,
        "low",
        1024,
        20,
        300,
        degrade_path="link-signal verdict only",
    ),
    TaskSpec(TaskId.competitor_owner_kind, "low", 1024, 20, 300, degrade_path="owner kind unknown"),
    TaskSpec(TaskId.competitor_evidence_summary, "low", 1024, 20, 400, degrade_path="deterministic summary"),
    TaskSpec(
        TaskId.topic_adjudication,
        "low",
        3072,
        25,
        1200,
        cache_ttl_days=14,
        degrade_path="stricter lexical/semantic threshold with review flag",
    ),
    TaskSpec(TaskId.topic_alias_proposal, "low", 1024, 20, 400, degrade_path="no alias proposals"),
    TaskSpec(TaskId.directness_tiebreak, "low", 1024, 20, 300, degrade_path="deterministic directness"),
    TaskSpec(TaskId.news_judgement, "medium", 4096, 35, 1200, degrade_path="keyword overlap, confidence low"),
    TaskSpec(TaskId.refresh_phrasing, "low", 1024, 20, 300, degrade_path="keyword-only suggestion"),
    TaskSpec(TaskId.card_write, "medium", 4096, 30, 600, degrade_path="template card writer"),
    TaskSpec(TaskId.card_critique, "low", 2048, 20, 1000, degrade_path="no critique, lint only"),
    TaskSpec(TaskId.research_query_planning, "low", 512, 15, 200, degrade_path="query templates"),
)

for _spec in DEFAULT_TASKS:
    llm_task_registry.add(_spec.task.value, _spec)


def resolve_task(task: TaskId, settings: LLMSettings) -> TaskSpec:
    spec = llm_task_registry.get(task.value)
    override = settings.tasks.get(task.value)
    if override is None:
        return spec
    changes = {
        name: value
        for name, value in override.model_dump(exclude={"prompt_version"}).items()
        if value is not None
    }
    return replace(spec, **changes)
