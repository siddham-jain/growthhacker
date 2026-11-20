from collections import defaultdict
from collections.abc import Sequence
from statistics import median
from uuid import UUID

import numpy as np

from app.core.config import ReviewSettings
from app.evaluation.review.models import CriterionStat, RaterStat, Rating, ReviewItemRecord, ReviewSummary
from app.evaluation.stats.agreement import krippendorff_alpha, weighted_kappa
from app.evaluation.stats.bootstrap import item_bootstrap
from app.models.evaluation import REVIEW_CRITERIA

SCALE = [1, 2, 3, 4, 5]


def item_scores(ratings: Sequence[Rating]) -> dict[UUID, float]:
    """mean over raters and criteria per item."""
    grouped: dict[UUID, list[float]] = defaultdict(list)
    for rating in ratings:
        grouped[rating.item_id] += [getattr(rating, c) for c in REVIEW_CRITERIA]
    return {item: float(np.mean(values)) for item, values in grouped.items()}


def criterion_means(ratings: Sequence[Rating], criterion: str) -> dict[UUID, float]:
    grouped: dict[UUID, list[int]] = defaultdict(list)
    for rating in ratings:
        grouped[rating.item_id].append(getattr(rating, criterion))
    return {item: float(np.mean(values)) for item, values in grouped.items()}


def _mean(values: Sequence[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _group_means(items: Sequence[ReviewItemRecord], scores: dict[UUID, float], key) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for item in items:
        if item.item_id in scores:
            for group in key(item):
                grouped[group].append(scores[item.item_id])
    return {group: round(float(np.mean(values)), 4) for group, values in sorted(grouped.items())}


def _z_scores(ratings: Sequence[Rating]) -> dict[UUID, float]:
    # per-rater standardisation removes acquiescence (everything a 5) and harshness
    by_rater: dict[UUID, list[int]] = defaultdict(list)
    for rating in ratings:
        by_rater[rating.rater_id] += [getattr(rating, c) for c in REVIEW_CRITERIA]
    moments = {
        rater: (float(np.mean(values)), float(np.std(values)) or 1.0) for rater, values in by_rater.items()
    }
    grouped: dict[UUID, list[float]] = defaultdict(list)
    for rating in ratings:
        mean, sd = moments[rating.rater_id]
        grouped[rating.item_id] += [(getattr(rating, c) - mean) / sd for c in REVIEW_CRITERIA]
    return {item: float(np.mean(values)) for item, values in grouped.items()}


def summarise_review(
    batch_id: UUID,
    items: Sequence[ReviewItemRecord],
    ratings: Sequence[Rating],
    settings: ReviewSettings,
    seed: int,
) -> ReviewSummary:
    decoy_ids = {item.item_id for item in items if item.is_decoy}
    known = {item.item_id for item in items}
    ratings = [r for r in ratings if r.item_id in known]
    real = [r for r in ratings if r.item_id not in decoy_ids]
    decoys = [r for r in ratings if r.item_id in decoy_ids]
    scores = item_scores(real)
    order = sorted(scores, key=str)
    score_array = np.array([scores[item] for item in order])
    criterion_items = {c: criterion_means(real, c) for c in REVIEW_CRITERIA}
    criterion_arrays = {c: np.array([criterion_items[c][item] for item in order]) for c in REVIEW_CRITERIA}
    statistics = {"overall": lambda idx: float(score_array[idx].mean()) if idx.size else None}
    for criterion, values in criterion_arrays.items():
        statistics[criterion] = lambda idx, v=values: float(v[idx].mean()) if idx.size else None
    boot = (
        item_bootstrap(statistics, len(order), settings.bootstrap_resamples, np.random.default_rng(seed))
        if order
        else {}
    )
    raters = sorted({r.rater_id for r in ratings}, key=str)
    by_rater_item = {(r.item_id, r.rater_id): r for r in real}
    by_criterion = {}
    for criterion in REVIEW_CRITERIA:
        units = [
            [
                getattr(by_rater_item[(item, rater)], criterion) if (item, rater) in by_rater_item else None
                for rater in raters
            ]
            for item in order
        ]
        kappa = None
        if len(raters) >= 2:
            pairs = [(u[0], u[1]) for u in units if u[0] is not None and u[1] is not None]
            if pairs:
                kappa = weighted_kappa([a for a, _ in pairs], [b for _, b in pairs], SCALE, "quadratic")
        decoy_means = list(criterion_means(decoys, criterion).values())
        by_criterion[criterion] = CriterionStat(
            mean=_mean(list(criterion_items[criterion].values())),
            ci=boot[criterion].ci if boot else None,
            alpha_ordinal=krippendorff_alpha(units, "ordinal"),
            weighted_kappa=kappa,
            decoy_mean=_mean(decoy_means),
        )
    overall = _mean(list(scores.values()))
    decoy_scores = item_scores(decoys)
    decoy_overall = _mean(list(decoy_scores.values()))
    decoy_gap = None
    if decoy_overall is not None and overall is not None:
        decoy_gap = {"overall": overall - decoy_overall} | {
            c: (by_criterion[c].mean or 0.0) - (by_criterion[c].decoy_mean or 0.0)
            for c in REVIEW_CRITERIA
            if by_criterion[c].decoy_mean is not None
        }
    z = _z_scores(ratings)
    real_z = [z[i] for i in scores]
    decoy_z = [z[i] for i in decoy_scores]
    decoy_gap_z = float(np.mean(real_z) - np.mean(decoy_z)) if real_z and decoy_z else None
    informative = None if decoy_gap is None else decoy_gap["overall"] >= settings.decoy_gap_min
    criteria_pass = all((by_criterion[c].mean or 0.0) >= settings.min_criterion_mean for c in REVIEW_CRITERIA)
    passed = overall is not None and overall >= settings.target_mean and criteria_pass
    overall_ci = boot["overall"].ci if boot else None
    would_ship = _mean([1.0 if r.would_ship else 0.0 for r in real])
    warnings = _warnings(by_criterion, informative, would_ship, settings)
    rater_stats = []
    for rater in raters:
        values = [getattr(r, c) for r in real if r.rater_id == rater for c in REVIEW_CRITERIA]
        if values:
            rater_stats.append(
                RaterStat(
                    rater_id=str(rater),
                    n_ratings=len(values) // len(REVIEW_CRITERIA),
                    mean=float(np.mean(values)),
                    sd=float(np.std(values)),
                )
            )
    real_items = [item for item in items if not item.is_decoy]
    return ReviewSummary(
        batch_id=batch_id,
        n_items=len(scores),
        n_decoys=len(decoy_scores),
        n_raters=len(raters),
        overall_mean=overall,
        overall_ci=overall_ci,
        overall_median=float(median(scores.values())) if scores else None,
        by_criterion=by_criterion,
        share_items_ge4=_mean([1.0 if s >= 4 else 0.0 for s in scores.values()]),
        would_ship_share=would_ship,
        decoy_gap=decoy_gap,
        decoy_gap_z=decoy_gap_z,
        decoys_informative=informative,
        raters=tuple(rater_stats),
        by_type=_group_means(real_items, scores, lambda i: [i.type]),
        by_client=_group_means(real_items, scores, lambda i: [str(i.client_id)]),
        by_lens=_group_means(real_items, scores, lambda i: list(i.lens)),
        passed=passed,
        lower_bound_passes=bool(overall_ci and overall_ci[0] >= settings.target_mean),
        warnings=tuple(warnings),
    )


def _warnings(
    by_criterion: dict[str, CriterionStat],
    informative: bool | None,
    would_ship: float | None,
    settings: ReviewSettings,
) -> list[str]:
    warnings = []
    if informative is False:
        warnings.append(
            f"Raters scored decoy cards within {settings.decoy_gap_min} of real ones; ratings may not discriminate."
        )
    low_alpha = [
        c
        for c, stat in by_criterion.items()
        if stat.alpha_ordinal is not None and stat.alpha_ordinal < settings.alpha_min
    ]
    if low_alpha:
        warnings.append(
            f"Krippendorff's alpha below {settings.alpha_min} for {', '.join(low_alpha)}: run a calibration "
            "discussion and read per-rater means separately."
        )
    if would_ship is not None and would_ship < settings.would_ship_target:
        warnings.append(
            f"Only {would_ship:.0%} of ratings would ship the card (target {settings.would_ship_target:.0%})."
        )
    return warnings
