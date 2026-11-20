from collections import defaultdict

from app.domain.analytics import ConversionLens, DirectnessHit, OwnConversionMetrics
from app.domain.common import ChannelRole
from app.services.analytics.reports.common import DIRECTNESS_RANK
from app.services.analytics.reports.context import EngineData, WindowContext, report_builder_registry

CTA_TRAITS = ("has_booking_cta", "has_own_site_link", "has_phone", "has_whatsapp", "has_chapters")


def own_conversion(data: EngineData, top_terms: int) -> OwnConversionMetrics | None:
    # owner-authorised numbers only; data api counts are never mixed into these ratios
    analytics = data.inputs.own_analytics
    if analytics is None or analytics.connection.status != "active" or not analytics.video_period:
        return None
    periods = analytics.video_period
    gained = {row.video_id: row.subscribers_gained for row in periods}
    per_1k = {row.video_id: row.subscribers_gained / row.views * 1000 for row in periods if row.views > 0}
    sources: dict[str, dict[str, int]] = defaultdict(dict)
    for row in analytics.traffic_sources:
        if row.video_id:
            sources[row.video_id][row.source_type] = sources[row.video_id].get(row.source_type, 0) + row.views
    shares = {
        vid: {source: views / total for source, views in sorted(rows.items())}
        for vid, rows in sorted(sources.items())
        if (total := sum(rows.values())) > 0
    }
    terms: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in analytics.search_terms:
        if row.video_id:
            terms[row.video_id].append((row.term, row.views))
    top = {
        vid: sorted(items, key=lambda t: (-t[1], t[0]))[:top_terms] for vid, items in sorted(terms.items())
    }
    return OwnConversionMetrics(
        period=(min(r.period_start for r in periods), max(r.period_end for r in periods)),
        subscribers_gained_by_video=dict(sorted(gained.items())),
        subscribers_gained_per_1k_views=dict(sorted(per_1k.items())),
        traffic_source_share=shares,
        top_search_terms=top,
    )


def conversion_lens(ctx: WindowContext, channel_ids: set[str], *, per_channel_keys: bool) -> ConversionLens:
    data = ctx.data
    states = ctx.window_states(channel_ids)
    ids = {s.video_id for s in states}
    hits: dict[str, list[DirectnessHit]] = defaultdict(list)
    for item in data.inputs.directness:
        if item.video_id in ids:
            hits[item.buyer_question_ref_id].append(DirectnessHit(video_id=item.video_id, level=item.level))
    coverage: dict[str, tuple[int, int]] = {}
    for channel_id in sorted(channel_ids):
        channel_states = [s for s in states if s.channel_id == channel_id]
        for trait in CTA_TRAITS:
            with_trait = sum(
                getattr(data.traits[s.video_id].traits.description, trait) for s in channel_states
            )
            key = f"{channel_id}:{trait}" if per_channel_keys else trait
            coverage[key] = (with_trait, len(channel_states))
        malformed = sum(data.traits[s.video_id].malformed_links > 0 for s in channel_states)
        coverage[f"{channel_id}:has_malformed_link" if per_channel_keys else "has_malformed_link"] = (
            malformed,
            len(channel_states),
        )
    own_ids = data.available_channel_ids((ChannelRole.own,))
    return ConversionLens(
        answer_directness={
            bq: sorted(items, key=lambda h: (DIRECTNESS_RANK[h.level], h.video_id))
            for bq, items in sorted(hits.items())
        },
        comment_intent={vid: data.comment_signals[vid] for vid in sorted(ids) if vid in data.comment_signals},
        description_cta_coverage=coverage,
        own_channel=own_conversion(data, ctx.settings.reports.top_search_terms)
        if own_ids & channel_ids
        else None,
    )


@report_builder_registry.register("conversion")
class ConversionLensBuilder:
    """the conversion lens stays a separate block; nothing here is blended into view metrics."""

    kind = "conversion"

    def build(self, ctx: WindowContext) -> ConversionLens:
        channels = ctx.data.available_channel_ids((ChannelRole.own, ChannelRole.competitor))
        return conversion_lens(ctx, channels, per_channel_keys=True)
