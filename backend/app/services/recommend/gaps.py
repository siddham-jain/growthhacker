from datetime import datetime

from app.domain.analytics import GapCell, GapMatrix
from app.domain.common import DomainModel, WindowDays
from app.domain.recommendations import GapEvidence
from app.services.recommend.evidence.index import EvidenceIndex
from app.services.recommend.topics import TopicCatalog

OUTLIER_SHARE_SATURATION = 0.2


class GapReport(DomainModel):
    # gap judgement per client topic; the counts come from 06's gap matrix and the evidence index
    window_days: WindowDays
    as_of: datetime
    rows: list[GapEvidence]
    cells: dict[str, dict[str, GapCell]] = {}

    def row(self, topic_id: str) -> GapEvidence | None:
        return next((row for row in self.rows if row.topic_id == topic_id), None)


class GapClassifier:
    def __init__(self, *, half_life_days: float, stale_days: int, underperforming_ratio: float) -> None:
        self.half_life_days = half_life_days
        self.stale_days = stale_days
        self.underperforming_ratio = underperforming_ratio

    def report(
        self,
        catalog: TopicCatalog,
        index: EvidenceIndex,
        matrix: GapMatrix | None,
        competitor_ids: list[str],
    ) -> GapReport:
        cells = matrix.cells if matrix is not None else {}
        window_outliers = len(index.outliers)
        rows = [
            self.classify(topic.id, index, cells.get(topic.id, {}), competitor_ids, window_outliers)
            for topic in catalog.all()
        ]
        return GapReport(window_days=index.window_days, as_of=index.as_of, rows=rows, cells=dict(cells))

    def classify(
        self,
        topic_id: str,
        index: EvidenceIndex,
        cells: dict[str, GapCell],
        competitor_ids: list[str],
        window_outliers: int,
    ) -> GapEvidence:
        # coverage comes from 06's cells; outlier counts from the same index the reasons quote
        if cells:
            covering = sum(1 for channel_id in competitor_ids if cells.get(channel_id, _EMPTY).n_videos > 0)
        else:
            covering = len({video.channel_id for video in index.by_topic(topic_id)} & set(competitor_ids))
        niche_outliers = sum(1 for video in index.by_topic(topic_id) if video.is_outlier)
        comp_share = covering / max(1, len(competitor_ids))
        outlier_share = niche_outliers / max(1, window_outliers)
        demand = 0.5 * comp_share + 0.5 * min(1.0, outlier_share / OUTLIER_SHARE_SATURATION)

        own = index.own_by_topic(topic_id)
        ratios = [video.ratio for video in own if video.ratio is not None]
        best_ratio = max(ratios) if ratios else None
        presence = max((2 ** (-video.age_days / self.half_life_days) for video in own), default=0.0)
        underperforming = best_ratio is not None and best_ratio < self.underperforming_ratio
        if underperforming:
            presence *= 0.5
        gap_size = demand * (1 - presence)
        last_covered = own[0].published_at if own else None

        if not own and demand > 0:
            gap_type = "uncovered"
        elif own and own[0].age_days > self.stale_days and demand > 0:
            gap_type = "stale"
        elif underperforming and niche_outliers > 0:
            gap_type = "underperforming"
        else:
            gap_type = "none"
        return GapEvidence(
            topic_id=topic_id,
            gap_type=gap_type,
            competitors_covering=covering,
            competitors_total=len(competitor_ids),
            niche_outliers_matching=niche_outliers,
            window_outliers_total=window_outliers,
            client_videos_matching=len(own),
            client_last_covered_at=last_covered.date() if last_covered else None,
            client_best_ratio=round(best_ratio, 2) if best_ratio is not None else None,
            gap_size=round(min(1.0, max(0.0, gap_size)), 4),
        )


_EMPTY = GapCell(n_videos=0, n_outliers=0, best_ratio=None, latest_published_at=None)
