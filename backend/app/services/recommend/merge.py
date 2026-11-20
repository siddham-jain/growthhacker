from collections import defaultdict
from dataclasses import dataclass, field

from app.domain.recommendations import PatternSpec, RecommendationDraft, RecType
from app.services.recommend.context import RecommendContext
from app.services.recommend.stats import jaccard

NEVER_MERGED = frozenset({RecType.creator, RecType.description_refresh, RecType.news_opportunity})
POOL_LIMIT = 12


@dataclass(slots=True)
class MergeResult:
    kept: list[RecommendationDraft]
    dropped: list[tuple[RecommendationDraft, str, dict]] = field(default_factory=list)


def pool_ids(draft: RecommendationDraft) -> set[str]:
    return {ref.video_id for ref in draft.reference_pool}


class CandidateMerger:
    """dedupes candidates in a fixed order: exact key, overlapping evidence, subsumed patterns, news, contradictions."""

    def merge(self, drafts: list[RecommendationDraft], ctx: RecommendContext) -> MergeResult:
        result = MergeResult(kept=[])
        survivors = self._exact(drafts, result)
        survivors = self._overlapping(survivors, ctx, result)
        survivors = self._subsumed(survivors, ctx, result)
        survivors = self._cross_component(survivors, ctx, result)
        result.kept = self._exclusive_values(survivors)
        return result

    @staticmethod
    def _ordered(drafts: list[RecommendationDraft]) -> list[RecommendationDraft]:
        return sorted(drafts, key=lambda draft: (-len(draft.reference_pool), draft.stable_key))

    def _exact(self, drafts: list[RecommendationDraft], result: MergeResult) -> list[RecommendationDraft]:
        by_key: dict[str, RecommendationDraft] = {}
        for draft in self._ordered(drafts):
            if draft.stable_key in by_key:
                result.dropped.append((draft, "duplicate_key", {}))
            else:
                by_key[draft.stable_key] = draft
        return list(by_key.values())

    def _overlapping(
        self, drafts: list[RecommendationDraft], ctx: RecommendContext, result: MergeResult
    ) -> list[RecommendationDraft]:
        survivors: list[RecommendationDraft] = []
        ranked = sorted(
            drafts,
            key=lambda draft: (-self._intent(draft, ctx), -len(draft.reference_pool), draft.stable_key),
        )
        for draft in ranked:
            if draft.type in NEVER_MERGED:
                survivors.append(draft)
                continue
            target = next(
                (
                    index
                    for index, survivor in enumerate(survivors)
                    if survivor.type is draft.type and self._overlaps(survivor, draft, ctx)
                ),
                None,
            )
            if target is None:
                survivors.append(draft)
                continue
            overlap = round(jaccard(pool_ids(draft), pool_ids(survivors[target])), 3)
            survivors[target] = self._absorb(survivors[target], draft)
            result.dropped.append(
                (draft, "merged_into", {"survivor": survivors[target].stable_key, "jaccard": overlap})
            )
        return survivors

    def _overlaps(self, left: RecommendationDraft, right: RecommendationDraft, ctx: RecommendContext) -> bool:
        settings = ctx.settings
        overlap = jaccard(pool_ids(left), pool_ids(right))
        if overlap >= settings.merge_jaccard:
            return True
        similar_topics = any(
            ctx.catalog.similarity(a, b) >= settings.merge_topic_similarity
            for a in left.topic_ids
            for b in right.topic_ids
        )
        return similar_topics and overlap >= settings.merge_topic_jaccard

    @staticmethod
    def _intent(draft: RecommendationDraft, ctx: RecommendContext) -> float:
        topic = ctx.catalog.for_question(draft.buyer_question_id)
        return topic.intent if topic else 0.0

    @staticmethod
    def _absorb(survivor: RecommendationDraft, other: RecommendationDraft) -> RecommendationDraft:
        # pools are unioned and re-ranked; reasons stay the survivor's own, never concatenated
        refs = {ref.video_id: ref for ref in [*survivor.reference_pool, *other.reference_pool]}
        pool = sorted(
            refs.values(),
            key=lambda ref: (
                not ref.is_outlier,
                -(ref.outlier_ratio or 0.0),
                -ref.published_at.timestamp(),
                ref.video_id,
            ),
        )[:POOL_LIMIT]
        related = list(survivor.related_buyer_question_ids)
        for question in [other.buyer_question_id, *other.related_buyer_question_ids]:
            if question is not None and question != survivor.buyer_question_id and question not in related:
                related.append(question)
        return survivor.model_copy(update={"reference_pool": pool, "related_buyer_question_ids": related})

    def _subsumed(
        self, drafts: list[RecommendationDraft], ctx: RecommendContext, result: MergeResult
    ) -> list[RecommendationDraft]:
        singles = [
            draft
            for draft in drafts
            if isinstance(draft.subject, PatternSpec) and len(draft.subject.traits) == 1
        ]
        kept = []
        for draft in drafts:
            subject = draft.subject
            if isinstance(subject, PatternSpec) and len(subject.traits) > 1:
                cover = next(
                    (
                        single
                        for single in singles
                        if single.type is draft.type
                        and set(single.subject.traits.items()) <= set(subject.traits.items())
                        and abs(single.subject.lift - subject.lift) < ctx.settings.subsume_lift_delta
                        and subject.n_with < single.subject.n_with
                    ),
                    None,
                )
                if cover is not None:
                    result.dropped.append((draft, "subsumed_pattern", {"by": cover.stable_key}))
                    continue
            kept.append(draft)
        return kept

    def _cross_component(
        self, drafts: list[RecommendationDraft], ctx: RecommendContext, result: MergeResult
    ) -> list[RecommendationDraft]:
        news = [draft for draft in drafts if draft.type is RecType.news_opportunity]
        kept = []
        for draft in drafts:
            if draft.type is RecType.video_idea:
                if "event_driven" in draft.flags:
                    result.dropped.append((draft, "event_driven", {}))
                    continue
                newer = next(
                    (
                        item
                        for item in news
                        if set(item.topic_ids) & set(draft.topic_ids)
                        and jaccard(pool_ids(item), pool_ids(draft)) >= ctx.settings.news_overlap_jaccard
                    ),
                    None,
                )
                if newer is not None:
                    result.dropped.append((draft, "superseded_by_news", {"by": newer.stable_key}))
                    continue
            kept.append(draft)
        return kept

    @staticmethod
    def _exclusive_values(drafts: list[RecommendationDraft]) -> list[RecommendationDraft]:
        # two values of the same single trait both lifted (e.g. short and long bands) is mixed evidence
        groups: dict[tuple[RecType, str], set[str]] = defaultdict(set)
        for draft in drafts:
            subject = draft.subject
            if isinstance(subject, PatternSpec) and len(subject.traits) == 1:
                ((key, value),) = subject.traits.items()
                groups[(draft.type, key)].add(value)
        marked = []
        for draft in drafts:
            subject = draft.subject
            if isinstance(subject, PatternSpec) and len(subject.traits) == 1:
                ((key, _),) = subject.traits.items()
                if len(groups[(draft.type, key)]) > 1 and "contradictory" not in draft.flags:
                    draft = draft.model_copy(update={"flags": [*draft.flags, "contradictory"]})
            marked.append(draft)
        return marked
