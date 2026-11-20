from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from app.core.config import NewsSettings
from app.domain.news import NewsItem, NewsStory, SourceTier
from app.services.news.context import NewsClientContext
from app.services.news.text import contains_phrase, partial_ratio
from app.services.news.tiers import TIER1

TIER_WEIGHT = {
    SourceTier.tier1_primary: 1.0,
    SourceTier.tier1_authority: 1.0,
    SourceTier.tier2_established: 0.8,
    SourceTier.tier3_low: 0.4,
    SourceTier.blocked: 0.0,
}
LEXICAL_WEIGHT = 0.6
SEMANTIC_WEIGHT = 0.4


@dataclass(frozen=True)
class StoryBundle:
    story: NewsStory
    items: list[NewsItem]

    @property
    def dated(self) -> bool:
        return any(item.published_precision != "none" for item in self.items)


@dataclass(frozen=True)
class PrefilteredStory:
    bundle: StoryBundle
    score: float
    client_hit: bool
    competitor_hit: bool


def _term_score(term: str, item: NewsItem) -> float:
    if contains_phrase(item.title, term):
        return 1.0
    if contains_phrase(item.snippet, term):
        return 0.6
    return partial_ratio(term, item.title) / 100 * 0.4


class RelevancePrefilter:
    """cheap deterministic screen before any judgement call."""

    def __init__(self, settings: NewsSettings) -> None:
        self._settings = settings

    def _terms(self, ctx: NewsClientContext) -> list[str]:
        freshness = ctx.freshness
        terms = [name for entity in freshness.watch_entities for name in (entity.name, *entity.aliases)]
        terms += [name for offering in ctx.profile.offerings for name in (offering.name, *offering.aliases)]
        terms += [k.term for k in ctx.profile.niche_keywords if k.tier == "seed"]
        terms += [topic.query for topic in freshness.watch_topics]
        terms += [window_term for window in freshness.seasonality for window_term in window.query_terms]
        return [term for term in dict.fromkeys(terms) if len(term.strip()) >= 3]

    def _competitor_terms(self, ctx: NewsClientContext) -> list[str]:
        return [name for c in ctx.confirmed_competitors for name in (c.brand_name, *c.aliases)]

    def in_window(self, bundle: StoryBundle, ctx: NewsClientContext, now: datetime) -> bool:
        envelope = ctx.envelope(self._settings)
        days = envelope.recency_days
        if bundle.story.best_tier is SourceTier.tier1_authority:
            days = max(days, self._settings.long_lived_event_recency_days)
        if bundle.story.first_published_at < now - timedelta(days=days):
            return False
        if not bundle.dated and bundle.story.best_tier is not SourceTier.tier1_primary:
            return False
        return not all(
            "continuously_updated" in item.flags or "social" in item.flags for item in bundle.items
        )

    def language_ok(self, bundle: StoryBundle, ctx: NewsClientContext) -> bool:
        languages = set(ctx.languages)
        return any(
            item.language is None or item.language in languages or item.source_tier in TIER1
            for item in bundle.items
        )

    def select(
        self,
        bundles: Sequence[StoryBundle],
        ctx: NewsClientContext,
        now: datetime,
        *,
        business_vector: NDArray[np.float32] | None = None,
        centroids: Mapping[UUID, NDArray[np.float32]] | None = None,
    ) -> list[PrefilteredStory]:
        terms = self._terms(ctx)
        competitor_terms = self._competitor_terms(ctx)
        brand_terms = ctx.brand_terms
        centroids = centroids or {}
        kept: list[PrefilteredStory] = []
        for bundle in bundles:
            if not self.in_window(bundle, ctx, now) or not self.language_ok(bundle, ctx):
                continue
            lexical = max((_term_score(t, item) for t in terms for item in bundle.items), default=0.0)
            client_hit = any(
                contains_phrase(f"{i.title} {i.snippet}", t) for t in brand_terms for i in bundle.items
            )
            competitor_hit = any(
                contains_phrase(f"{i.title} {i.snippet}", t) for t in competitor_terms for i in bundle.items
            )
            centroid = centroids.get(bundle.story.id)
            if business_vector is not None and centroid is not None:
                score = LEXICAL_WEIGHT * lexical + SEMANTIC_WEIGHT * max(
                    0.0, float(np.dot(centroid, business_vector))
                )
            else:
                score = lexical
            if score >= self._settings.prefilter_min or client_hit or competitor_hit:
                kept.append(PrefilteredStory(bundle, score, client_hit, competitor_hit))
        kept.sort(
            key=lambda p: (
                p.client_hit,
                p.score * TIER_WEIGHT[p.bundle.story.best_tier],
                p.bundle.story.id.hex,
            ),
            reverse=True,
        )
        return kept[: ctx.envelope(self._settings).max_judged_per_run]
