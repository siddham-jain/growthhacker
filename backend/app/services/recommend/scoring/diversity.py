from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.recommendations import PatternSpec, RecommendationDraft, ReferenceVideo, VideoIdeaSubject
from app.services.recommend.context import RecommendContext
from app.services.recommend.evidence.references import ReferenceSelector
from app.services.recommend.scoring.scorer import tie_break_key
from app.services.recommend.stats import jaccard

# re-annotates, re-gates and re-scores a draft with swapped references; none when it no longer passes
Reannotate = Callable[[RecommendationDraft, list[ReferenceVideo]], RecommendationDraft | None]


def shape_key(draft: RecommendationDraft) -> str | None:
    subject = draft.subject
    if isinstance(subject, VideoIdeaSubject):
        return f"format_key={subject.format_key}" if subject.format_key else None
    if isinstance(subject, PatternSpec):
        return "|".join(f"{key}={value}" for key, value in sorted(subject.traits.items()))
    return None


def similarity(left: RecommendationDraft, right: RecommendationDraft, ctx: RecommendContext) -> float:
    refs = jaccard((ref.video_id for ref in left.references), (ref.video_id for ref in right.references))
    topical = any(
        ctx.catalog.similarity(a, b) >= ctx.settings.merge_topic_similarity
        for a in left.topic_ids
        for b in right.topic_ids
    )
    left_shape, right_shape = shape_key(left), shape_key(right)
    same_shape = left_shape is not None and left_shape == right_shape
    return 0.4 * refs + 0.3 * topical + 0.2 * same_shape + 0.1 * (left.type is right.type)


@dataclass(slots=True)
class Selection:
    selected: list[RecommendationDraft] = field(default_factory=list)
    queue: list[RecommendationDraft] = field(default_factory=list)
    skipped: list[tuple[RecommendationDraft, str]] = field(default_factory=list)


class MmrSelector:
    """greedy maximal marginal relevance (carbonell & goldstein 1998) with per-type caps and reference reuse caps."""

    def __init__(self, ctx: RecommendContext, references: ReferenceSelector, reannotate: Reannotate) -> None:
        self.ctx = ctx
        self.references = references
        self.reannotate = reannotate

    def select(self, candidates: list[RecommendationDraft], slots: int) -> Selection:
        selection = Selection(queue=sorted(candidates, key=tie_break_key))
        self.fill(selection, slots)
        return selection

    def fill(self, selection: Selection, slots: int) -> None:
        self._fill(selection, slots, extra=0)
        capped = [draft for draft, reason in selection.skipped if reason == "type_cap"]
        if len(selection.selected) < slots and capped:
            # caps keep the mix varied; slots no other type can fill go to capped drafts that passed every gate
            selection.skipped = [item for item in selection.skipped if item[1] != "type_cap"]
            selection.queue = sorted([*selection.queue, *capped], key=tie_break_key)
            self._fill(selection, slots, extra=self.ctx.settings.type_cap_spill)

    def _fill(self, selection: Selection, slots: int, extra: int) -> None:
        settings = self.ctx.settings
        while len(selection.selected) < slots and selection.queue:
            best = self._best(selection)
            selection.queue.remove(best)
            cap = settings.type_caps.get(best.type.value, 0)
            if sum(1 for item in selection.selected if item.type is best.type) >= cap + (extra if cap else 0):
                selection.skipped.append((best, "type_cap"))
                continue
            placed = self._respect_reuse(best, selection.selected)
            if placed is None:
                selection.skipped.append((best, "ref_overused"))
                continue
            selection.selected.append(placed)

    def _best(self, selection: Selection) -> RecommendationDraft:
        # the queue is in tie-break order, so the first draft with the top marginal relevance wins
        best, best_value = selection.queue[0], float("-inf")
        for draft in selection.queue:
            value = self._marginal(draft, selection.selected)
            if value > best_value:
                best, best_value = draft, value
        return best

    def _marginal(self, draft: RecommendationDraft, selected: list[RecommendationDraft]) -> float:
        lam = self.ctx.settings.mmr_lambda
        total = draft.score.total if draft.score else 0.0
        penalty = max((similarity(draft, item, self.ctx) for item in selected), default=0.0)
        return round(lam * total - (1 - lam) * penalty, 9)

    def _respect_reuse(
        self, draft: RecommendationDraft, selected: list[RecommendationDraft]
    ) -> RecommendationDraft | None:
        limit = self.ctx.settings.max_ref_reuse
        usage = Counter(ref.video_id for item in selected for ref in item.references)
        overused = {video_id for video_id, count in usage.items() if count >= limit}
        if not overused & {ref.video_id for ref in draft.references}:
            return draft
        swapped = self.references.select(draft, exclude=overused)
        if len(swapped) < self.ctx.settings.gates.min_refs(draft.type.value):
            return None
        return self.reannotate(draft, swapped)
