from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReviewReference(_Model):
    title: str
    channel: str
    url: str
    published_on: date
    views_label: str
    outlier_ratio: float | None = None
    thumbnail_url: str | None = None


class ReviewCard(_Model):
    # only what the product shows a client; ids, arm and gate features stay off the card
    client_name: str
    type: str
    summary: str
    lens: tuple[str, ...]
    buyer_question: str | None = None
    why_it_works: tuple[str, ...]
    reference_videos: tuple[ReviewReference, ...]
    fit_to_client: str
    expected_range: str | None = None
    confidence: str


class ClientSheet(_Model):
    client_id: UUID
    name: str
    website: str | None = None
    summary: str | None = None
    offerings: tuple[str, ...] = ()
    buyer_questions: tuple[str, ...] = ()
    competitors: tuple[str, ...] = ()
    window_days: int


class UniverseVideo(_Model):
    video_id: str
    channel_id: str
    channel_title: str
    title: str
    published_at: datetime
    views: int
    thumbnail_url: str | None = None


class SourceCard(_Model):
    rec_id: str | None
    db_id: UUID | None = None
    type: str
    lens: tuple[str, ...]
    confidence_label: str
    card: ReviewCard
    gate_features: dict[str, float] = {}


class ClientCards(_Model):
    client_id: UUID
    workspace_id: UUID
    run_id: UUID
    as_of: datetime
    window_days: int
    sheet: ClientSheet
    cards: tuple[SourceCard, ...]
    universe: tuple[UniverseVideo, ...] = ()
    oldest_api_at: datetime


class ReviewItemDraft(_Model):
    item_id: UUID
    batch_id: UUID
    workspace_id: UUID
    client_id: UUID
    is_decoy: bool
    source: SourceCard
    display_seed: int
    expires_at: datetime


class ReviewSampleDesign(_Model):
    batch_id: UUID
    items: tuple[ReviewItemDraft, ...]
    sheets: tuple[ClientSheet, ...]
    by_type: dict[str, int]
    notes: tuple[str, ...] = ()


class ReviewItemRecord(_Model):
    item_id: UUID
    batch_id: UUID
    workspace_id: UUID | None
    client_id: UUID | None
    is_decoy: bool
    rec_id: str | None
    type: str
    lens: tuple[str, ...]
    confidence_label: str
    card: ReviewCard | None
    gate_features: dict[str, float]


class Rating(_Model):
    item_id: UUID
    rater_id: UUID
    specific: int = Field(ge=1, le=5)
    evidenced: int = Field(ge=1, le=5)
    relevant: int = Field(ge=1, le=5)
    actionable: int = Field(ge=1, le=5)
    not_obvious: int = Field(ge=1, le=5)
    would_ship: bool
    comment: str | None = None
    rated_at: datetime


class CriterionStat(_Model):
    mean: float | None
    ci: tuple[float, float] | None
    alpha_ordinal: float | None
    weighted_kappa: float | None
    decoy_mean: float | None


class RaterStat(_Model):
    rater_id: str
    n_ratings: int
    mean: float
    sd: float


class ReviewSummary(_Model):
    batch_id: UUID
    n_items: int
    n_decoys: int
    n_raters: int
    overall_mean: float | None
    overall_ci: tuple[float, float] | None
    overall_median: float | None
    by_criterion: dict[str, CriterionStat]
    share_items_ge4: float | None
    would_ship_share: float | None
    decoy_gap: dict[str, float] | None
    decoy_gap_z: float | None
    decoys_informative: bool | None
    raters: tuple[RaterStat, ...]
    by_type: dict[str, float]
    by_client: dict[str, float]
    by_lens: dict[str, float]
    passed: bool
    lower_bound_passes: bool
    warnings: tuple[str, ...]


class GateProposal(_Model):
    feature: str
    spearman_rho: float
    n_items: int
    direction: str
    current_threshold: float | None
    proposed_threshold: float
    retained_share: float
    retained_mean_rating: float
    config_diff: dict
    rationale: str
