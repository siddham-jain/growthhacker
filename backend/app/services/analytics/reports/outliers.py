from app.domain.analytics import LiftScope, OutlierRecord
from app.services.analytics.outliers import OUTLIER_TIERS
from app.services.analytics.reports.common import ratio_key, views_summary
from app.services.analytics.reports.context import WindowContext, report_builder_registry

SPIKE_REASONS = frozenset({"views_without_engagement", "tiny_baseline"})


@report_builder_registry.register("outliers")
class OutlierListBuilder:
    """outliers per scope sorted by ratio; suppressed spikes are listed apart and never used as evidence."""

    kind = "outliers"

    def build(
        self, ctx: WindowContext
    ) -> tuple[dict[str, list[OutlierRecord]], dict[str, list[OutlierRecord]]]:
        outliers: dict[str, list[OutlierRecord]] = {}
        spikes: dict[str, list[OutlierRecord]] = {}
        for scope, view in sorted(ctx.scopes.items()):
            found, unusual = [], []
            for state in view.states:
                assessment = ctx.display(state)
                if assessment is None:
                    continue
                if assessment.is_outlier:
                    found.append((state, assessment))
                elif assessment.tier in OUTLIER_TIERS and assessment.suppressed_reason in SPIKE_REASONS:
                    unusual.append((state, assessment))
            outliers[scope.value] = [
                self._record(ctx, scope, s, a) for s, a in sorted(found, key=lambda x: ratio_key(*x))
            ]
            spikes[scope.value] = [
                self._record(ctx, scope, s, a) for s, a in sorted(unusual, key=lambda x: ratio_key(*x))
            ]
        return outliers, spikes

    @staticmethod
    def _record(ctx: WindowContext, scope: LiftScope, state, assessment) -> OutlierRecord:
        traits = ctx.data.traits.get(state.video_id)
        return OutlierRecord(
            video_id=state.video_id,
            channel_id=state.channel_id,
            scope=scope,
            published_at=state.published_at,
            views_at_age=views_summary(state, assessment),
            ratio=assessment.ratio,
            tier=assessment.tier,
            baseline_n=assessment.baseline_n,
            topic_ref_ids=[t.topic_ref_id for t in traits.traits.topics] if traits else [],
            segment=state.segment,
            url=state.video.url,
        )
