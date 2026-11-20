from uuid import UUID

from app.domain.common import DomainModel


class TopicEngineReport(DomainModel):
    # one per run per client; feeds run events and the ops report
    client_id: UUID
    run_id: UUID | None
    universe_hash: str
    engine_version: str
    videos_considered: int
    videos_matched: int
    videos_reused: int
    coverage_by_channel: dict[str, float] = {}
    matches_by_method: dict[str, int] = {}
    matches_by_confidence: dict[str, int] = {}
    pairs_implicitly_rejected: int = 0
    review_items_created: int = 0
    directness_rows: int = 0
    llm_calls: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_cost_usd: float = 0.0
    adjudication_cache_hits: int = 0
    embed_cache_hit_rate: float = 0.0
    unmatched_by_reason: dict[str, int] = {}
    unmatched_by_script: dict[str, int] = {}
    timings_ms: dict[str, int] = {}
    degraded_modes: list[str] = []
    warnings: list[str] = []
