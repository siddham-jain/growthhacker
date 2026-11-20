from collections.abc import Sequence

import numpy as np

from app.core.config import ReviewSettings
from app.evaluation.review.models import GateProposal, Rating, ReviewItemRecord
from app.evaluation.review.rubric import KNOBS
from app.evaluation.review.scorer import criterion_means, item_scores
from app.evaluation.stats.inference import spearman
from app.models.evaluation import REVIEW_CRITERIA

MIN_ITEMS = 5


def propose_gate_changes(
    items: Sequence[ReviewItemRecord], ratings: Sequence[Rating], settings: ReviewSettings
) -> list[GateProposal]:
    """for each gate feature: spearman rho with the item score and a retention curve; a threshold is
    proposed only when the kept cards average the target with enough cards kept. n is small, so every
    proposal is exploratory and needs a human to accept it."""
    real = {item.item_id: item for item in items if not item.is_decoy}
    real_ratings = [r for r in ratings if r.item_id in real]
    scores = item_scores(real_ratings)
    means = (
        {c: float(np.mean(list(criterion_means(real_ratings, c).values()))) for c in REVIEW_CRITERIA}
        if scores
        else {}
    )
    weakest = min(means, key=means.__getitem__) if means else None
    features = sorted({key for item in real.values() for key in item.gate_features})
    proposals = []
    for feature in features:
        pairs = [
            (real[item_id].gate_features[feature], score)
            for item_id, score in scores.items()
            if feature in real[item_id].gate_features
        ]
        if len(pairs) < MIN_ITEMS:
            continue
        values = [v for v, _ in pairs]
        rho = spearman(values, [s for _, s in pairs])
        if rho is None or rho == 0:
            continue
        proposal = _best_cut(feature, pairs, rho, len(scores), settings, weakest)
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def _best_cut(
    feature: str,
    pairs: Sequence[tuple[float, float]],
    rho: float,
    n_scored: int,
    settings: ReviewSettings,
    weakest: str | None,
) -> GateProposal | None:
    higher_is_better = rho > 0
    best: tuple[float, float, float] | None = None
    for cut in sorted({v for v, _ in pairs}):
        kept = [s for v, s in pairs if (v >= cut if higher_is_better else v <= cut)]
        share = len(kept) / n_scored
        mean = float(np.mean(kept)) if kept else 0.0
        meets = share < 1 and share >= settings.retention_min and mean >= settings.target_mean
        if meets and (best is None or share > best[1]):
            best = (cut, share, mean)
    if best is None:
        return None
    cut, share, mean = best
    bound = "min" if higher_is_better else "max"
    sign = ">=" if higher_is_better else "<="
    rationale = (
        f"exploratory (n={len(pairs)}): spearman rho {rho:.2f} between {feature} and the card rating; keeping "
        f"cards with {feature} {sign} {cut:g} retains {share:.0%} of cards with a mean rating of {mean:.2f}."
    )
    if weakest is not None:
        rationale += f" Lowest criterion is {weakest}; matching knob: {KNOBS[weakest]}."
    return GateProposal(
        feature=feature,
        spearman_rho=rho,
        n_items=len(pairs),
        direction="higher_is_better" if higher_is_better else "lower_is_better",
        current_threshold=None,
        proposed_threshold=cut,
        retained_share=share,
        retained_mean_rating=mean,
        config_diff={"recommend": {"gate_features": {feature: {bound: cut}}}},
        rationale=rationale,
    )
