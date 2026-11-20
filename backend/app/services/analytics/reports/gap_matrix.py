from datetime import timedelta

from app.domain.analytics import GapCell, GapMatrix
from app.domain.common import ChannelRole
from app.services.analytics.reports.common import best_directness
from app.services.analytics.reports.context import WindowContext, report_builder_registry


@report_builder_registry.register("gap_matrix")
class GapMatrixBuilder:
    """counts only (topic x channel over 365 days); 07 decides what counts as a gap."""

    kind = "gap_matrix"

    def build(self, ctx: WindowContext) -> GapMatrix:
        data = ctx.data
        lookback = data.as_of - timedelta(days=ctx.settings.reports.gap_lookback_days)
        channels = data.available_channel_ids((ChannelRole.own, ChannelRole.competitor))
        states = [
            s for s in data.published_between(lookback, data.as_of, channels) if not s.duplicate_secondary
        ]
        cells: dict[str, dict[str, GapCell]] = {}
        for ref in sorted(data.inputs.topic_refs, key=lambda r: r.id):
            if ref.status != "active":
                continue
            row: dict[str, GapCell] = {}
            for channel_id in sorted(channels):
                matching = [
                    (s, topic)
                    for s in states
                    if s.channel_id == channel_id
                    for topic in data.traits[s.video_id].traits.topics
                    if topic.topic_ref_id == ref.id
                ]
                assessed = [a for a in (ctx.display(s) for s, _ in matching) if a is not None]
                row[channel_id] = GapCell(
                    n_videos=len(matching),
                    n_outliers=sum(a.is_outlier for a in assessed),
                    best_ratio=max((a.ratio for a in assessed), default=None),
                    latest_published_at=max((s.published_at for s, _ in matching), default=None),
                    best_directness=best_directness(topic.directness for _, topic in matching),
                )
            cells[ref.id] = row
        return GapMatrix(window_days=ctx.window_days, as_of=data.as_of, cells=cells)
