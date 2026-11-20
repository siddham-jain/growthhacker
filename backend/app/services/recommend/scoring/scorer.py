from app.domain.recommendations import RecommendationDraft, ScoreBreakdown
from app.services.recommend.confidence import LABEL_RANK, outlier_counts
from app.services.recommend.context import RecommendContext
from app.services.recommend.scoring.components import score_component_registry
from app.services.recommend.scoring.penalties import penalty_registry


class Scorer:
    """internal card ordering only (weighted components x penalties); the number is never shown."""

    def score(self, draft: RecommendationDraft, ctx: RecommendContext) -> RecommendationDraft:
        weights = dict(ctx.settings.lens_weights[ctx.lens_mode])
        if not ctx.index.has_client_history and "gap" in weights:
            # with no client videos every gap looks the same, so its weight moves to evidence and intent
            moved = weights.pop("gap")
            weights["evidence"] = weights.get("evidence", 0.0) + moved / 2
            weights["intent"] = weights.get("intent", 0.0) + moved / 2
        components = {
            name: round(score_component_registry.get(name)().value(draft, ctx), 6)
            for name in sorted(score_component_registry)
            if name in weights
        }
        penalties = {
            name: round(penalty_registry.get(name)().factor(draft, ctx), 6)
            for name in sorted(penalty_registry)
        }
        total = sum(weights[name] * value for name, value in components.items())
        for factor in penalties.values():
            total *= factor
        breakdown = ScoreBreakdown(
            components=components, weights=weights, penalties=penalties, total=round(total, 6)
        )
        return draft.model_copy(update={"score": breakdown})


def tie_break_key(draft: RecommendationDraft) -> tuple:
    # total, confidence, channels, outliers, newest median reference, stable key: never input order
    outliers, channels = outlier_counts(draft)
    dates = sorted(ref.published_at.timestamp() for ref in draft.references)
    newest_median = dates[len(dates) // 2] if dates else 0.0
    confidence = LABEL_RANK[draft.confidence.label] if draft.confidence else -1
    total = round(draft.score.total, 6) if draft.score else 0.0
    return (-total, -confidence, -channels, -outliers, -newest_median, draft.stable_key)
