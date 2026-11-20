from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domain.common import DomainModel, UnitInterval, VideoId
from app.domain.confidence import Confidence
from app.domain.news import NewsSourceRef

MAX_OPERATIONS = 3
MAX_OPERATION_CHARS = 200
MAX_INSERTED_CHARS = 300
DESCRIPTION_BYTE_LIMIT = 5000


class InsertionKind(StrEnum):
    insert = "insert"
    link_fix = "link_fix"


class InsertionPosition(StrEnum):
    top_line = "top_line"
    after_first_paragraph = "after_first_paragraph"
    before_hashtag_line = "before_hashtag_line"
    before_links_block = "before_links_block"
    append = "append"


class ClaimLevel(StrEnum):
    # truth level of an insertion, from navigational to applicability claims
    l0_navigational = "L0_navigational"
    l1_context = "L1_context"
    l2_coverage = "L2_coverage"
    l3_applicability = "L3_applicability"


class InsertionAnchor(DomainModel):
    strategy: str
    anchor_excerpt: str = Field(max_length=80)
    anchor_hash: str
    char_offset_hint: int | None = None


class InsertionOp(DomainModel):
    # additive only; link_fix is the single exception and replaces exactly one broken url span
    op_id: str
    kind: InsertionKind
    position: InsertionPosition
    anchor: InsertionAnchor
    old_text: str | None = None
    text: str = Field(min_length=1, max_length=MAX_OPERATION_CHARS)
    text_bytes: int
    keywords_added: list[str] = []
    hashtags_added: list[str] = []
    claim_level: ClaimLevel
    trigger_ids: list[str] = []
    phrased_by: Literal["template", "llm"]

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "InsertionOp":
        if self.text_bytes != len(self.text.encode("utf-8")):
            raise ValueError("text_bytes must equal the utf-8 length of text")
        if self.kind is InsertionKind.insert:
            if self.old_text is not None:
                raise ValueError("insert operations never replace existing text")
            if "\n" in self.text or "\r" in self.text:
                raise ValueError("each insert is a single line")
        elif not self.old_text:
            raise ValueError("link_fix needs the exact broken span in old_text")
        return self


class TriggerEvidence(DomainModel):
    reference_video_ids: list[VideoId] = []
    news_sources: list[NewsSourceRef] = []
    owner_search_terms: list[str] = []
    defect: str | None = None
    notes: list[str] = []


class RefreshTrigger(DomainModel):
    id: str
    kind: str
    video_id: VideoId
    keyword: str | None = None
    strength: UnitInterval
    demand: UnitInterval
    urgency: UnitInterval
    evidence: TriggerEvidence
    dedupe_key: str


class Advisory(DomainModel):
    kind: Literal[
        "title_stale_year",
        "chapters_needed",
        "localizations_not_updated",
        "too_many_hashtags",
        "invalid_chapters",
        "shorts_links_not_clickable",
        "manual_review",
    ]
    message: str
    risk: Literal["low", "medium", "high"]


class RefreshStatus(StrEnum):
    proposed = "proposed"
    dismissed = "dismissed"
    applied_claimed = "applied_claimed"
    applied_detected = "applied_detected"
    expired = "expired"
    withdrawn = "withdrawn"
    superseded = "superseded"


def validate_operations(operations: list[InsertionOp]) -> None:
    if len(operations) > MAX_OPERATIONS:
        raise ValueError(f"at most {MAX_OPERATIONS} operations per video")
    inserted_chars = sum(len(op.text) for op in operations if op.kind is InsertionKind.insert)
    if inserted_chars > MAX_INSERTED_CHARS:
        raise ValueError(f"inserted text exceeds {MAX_INSERTED_CHARS} characters")


class DescriptionRefresh(DomainModel):
    id: str
    client_id: UUID
    video_id: VideoId
    run_id: UUID
    recommendation_id: str | None = None
    triggers: list[RefreshTrigger]
    operations: list[InsertionOp]
    advisories: list[Advisory] = []
    suggested_tags: list[str] = []
    keywords_added: list[str] = []
    inserted_bytes: int = Field(ge=0)
    resulting_bytes: int = Field(ge=0, le=DESCRIPTION_BYTE_LIMIT)
    expected_effect: str
    confidence: Confidence
    priority: float
    expires_at: AwareDatetime
    status: RefreshStatus
    description_hash_at_generation: str
    review_required: bool
    generated_at: AwareDatetime

    @model_validator(mode="after")
    def _within_edit_limits(self) -> "DescriptionRefresh":
        validate_operations(self.operations)
        if not self.operations and not self.advisories:
            raise ValueError("a refresh needs at least one operation or advisory")
        return self


class RefreshEligibility(DomainModel):
    client_id: UUID
    run_id: UUID
    decision: Literal["none", "hygiene_only", "periodic", "active"]
    cadence_days: int
    keyword_refresh_need: UnitInterval
    components: dict[str, float]
    evergreen_video_count: int
    eligible_video_count: int
    velocity_basis: Literal["snapshots", "lifetime_avg_provisional"]
    reasons: list[str] = []
    computed_at: AwareDatetime


class RefreshImpact(DomainModel):
    refresh_id: str
    applied_at: date
    applied_source: Literal["user", "detected"]
    pre_window_days: int
    post_window_days: int
    pre_velocity: float
    post_velocity: float
    ratio: float
    control_ratio: float | None = None
    adjusted_ratio: float | None = None
    owner_search_views_pre: int | None = None
    owner_search_views_post: int | None = None
    verdict: Literal["insufficient_data", "no_clear_change", "up", "down", "confounded"]
    caveats: list[str] = []
    computed_at: AwareDatetime
