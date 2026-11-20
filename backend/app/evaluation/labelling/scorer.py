from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

import numpy as np

from app.core.config import LabellingSettings
from app.evaluation.labelling.models import (
    GoldLabel,
    LabelAgreement,
    LabelRecord,
    OutlierEvalResult,
    TaskRecord,
    ThresholdPoint,
)
from app.evaluation.stats.agreement import cohen_kappa, gwet_ac1, percent_agreement
from app.evaluation.stats.bootstrap import item_bootstrap
from app.evaluation.stats.proportions import wilson
from app.evaluation.stats.sampling import f_beta, weighted_confusion

DEFINITE = ("outlier", "not_outlier")


def primary_labellers(labels: Sequence[LabelRecord]) -> tuple[UUID, ...]:
    return tuple(sorted({label.labeller_id for label in labels if label.round == 1}, key=str))


def resolve_gold(tasks: Sequence[TaskRecord], labels: Sequence[LabelRecord]) -> dict[UUID, GoldLabel]:
    """agreed labels stand; disagreements and unsure-vs-label pairs take the latest adjudication round;
    items both primary labellers marked unsure are excluded."""
    primary = primary_labellers(labels)[:2]
    by_task: dict[UUID, list[LabelRecord]] = defaultdict(list)
    for label in labels:
        by_task[label.task_id].append(label)
    gold: dict[UUID, GoldLabel] = {}
    for task in tasks:
        items = by_task.get(task.task_id, [])
        first = {label.labeller_id: label.label for label in items if label.round == 1}
        later = sorted(
            (label for label in items if label.round > 1), key=lambda lb: (lb.round, lb.labelled_at)
        )
        pair = [first[labeller] for labeller in primary if labeller in first]
        if len(primary) < 2 and len(pair) == 1:
            value = pair[0]
            gold[task.task_id] = GoldLabel(
                task_id=task.task_id,
                gold=value if value in DEFINITE else None,
                method="single" if value in DEFINITE else "excluded",
            )
        elif len(pair) < 2:
            gold[task.task_id] = GoldLabel(task_id=task.task_id, gold=None, method="pending")
        elif pair[0] == pair[1] == "unsure":
            gold[task.task_id] = GoldLabel(task_id=task.task_id, gold=None, method="excluded")
        elif pair[0] == pair[1]:
            gold[task.task_id] = GoldLabel(task_id=task.task_id, gold=pair[0], method="agree")
        else:
            decided = [label.label for label in later if label.label in DEFINITE]
            gold[task.task_id] = (
                GoldLabel(task_id=task.task_id, gold=decided[-1], method="adjudicated")
                if decided
                else GoldLabel(task_id=task.task_id, gold=None, method="pending")
            )
    return gold


def label_agreement(batch_id: UUID, labels: Sequence[LabelRecord]) -> LabelAgreement:
    primary = primary_labellers(labels)[:2]
    first: dict[UUID, dict[UUID, str]] = defaultdict(dict)
    for label in labels:
        if label.round == 1 and label.labeller_id in primary:
            first[label.task_id][label.labeller_id] = label.label
    pairs = [
        (row[primary[0]], row[primary[1]]) for row in first.values() if len(primary) == 2 and len(row) == 2
    ]
    unsure = {
        str(labeller): sum(1 for row in first.values() if row.get(labeller) == "unsure")
        for labeller in primary
    }
    if not pairs:
        return LabelAgreement(
            batch_id=batch_id,
            n_items=0,
            percent_agreement=None,
            cohen_kappa=None,
            gwet_ac1=None,
            n_unsure=unsure,
            labellers=tuple(str(p) for p in primary),
        )
    left, right = [a for a, _ in pairs], [b for _, b in pairs]
    return LabelAgreement(
        batch_id=batch_id,
        n_items=len(pairs),
        percent_agreement=percent_agreement(left, right),
        cohen_kappa=cohen_kappa(left, right),
        gwet_ac1=gwet_ac1(left, right),
        n_unsure=unsure,
        labellers=tuple(str(p) for p in primary),
    )


@dataclass(frozen=True)
class GoldRow:
    task: TaskRecord
    gold: bool
    weight: float


def threshold_grid(settings: LabellingSettings) -> list[float]:
    steps = round((settings.sweep_max - settings.sweep_min) / settings.sweep_step)
    return [round(settings.sweep_min + i * settings.sweep_step, 4) for i in range(steps + 1)]


def _weighted(rows: Sequence[GoldRow], predicted: Sequence[bool]) -> tuple[float | None, float | None]:
    confusion = weighted_confusion([r.gold for r in rows], predicted, [r.weight for r in rows])
    return confusion.precision, confusion.recall


def choose_threshold(points: Sequence[ThresholdPoint], min_precision: float) -> float | None:
    """max f0.5 subject to precision >= min_precision; ties go to the higher threshold (99 c12)."""
    eligible = [
        p
        for p in points
        if p.precision_w is not None and p.precision_w >= min_precision and p.f05_w is not None
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda p: (round(p.f05_w or 0.0, 12), p.threshold)).threshold


def _points(rows: Sequence[GoldRow], thresholds: Sequence[float]) -> list[ThresholdPoint]:
    points = []
    for threshold in thresholds:
        predicted = [(r.task.detector_ratio or 0.0) >= threshold for r in rows]
        precision, recall = _weighted(rows, predicted)
        points.append(
            ThresholdPoint(
                threshold=threshold,
                precision_w=precision,
                recall_w=recall,
                f05_w=f_beta(precision, recall, 0.5),
                f1_w=f_beta(precision, recall, 1.0),
                precision_ci=None,
                recall_ci=None,
            )
        )
    return points


def loco_optimism(
    rows: Sequence[GoldRow], thresholds: Sequence[float], chosen: float, min_precision: float
) -> float | None:
    """in-sample f0.5 at the chosen threshold minus f0.5 when each channel is predicted with a threshold
    picked without it."""
    channels = sorted({r.task.channel_id or "" for r in rows})
    if len(channels) < 2:
        return None
    predicted: list[bool] = []
    ordered: list[GoldRow] = []
    for channel in channels:
        held_in = [r for r in rows if (r.task.channel_id or "") != channel]
        held_out = [r for r in rows if (r.task.channel_id or "") == channel]
        fold = choose_threshold(_points(held_in, thresholds), min_precision) or chosen
        predicted += [(r.task.detector_ratio or 0.0) >= fold for r in held_out]
        ordered += held_out
    cv_precision, cv_recall = _weighted(ordered, predicted)
    in_precision, in_recall = _weighted(rows, [(r.task.detector_ratio or 0.0) >= chosen for r in rows])
    cv, in_sample = f_beta(cv_precision, cv_recall, 0.5), f_beta(in_precision, in_recall, 0.5)
    if cv is None or in_sample is None:
        return None
    return in_sample - cv


def _raw_counts(rows: Sequence[GoldRow], predicted: Sequence[bool]) -> dict[str, int]:
    counts = Counter()
    for row, flagged in zip(rows, predicted, strict=True):
        counts["tp" if row.gold and flagged else "fp" if flagged else "fn" if row.gold else "tn"] += 1
    return {key: counts[key] for key in ("tp", "fp", "fn", "tn")}


def _breakdown(rows: Sequence[GoldRow], key_of) -> dict[str, dict[str, float | int | None]]:
    groups: dict[str, list[GoldRow]] = defaultdict(list)
    for row in rows:
        for key in key_of(row):
            groups[key].append(row)
    result = {}
    for key, members in sorted(groups.items()):
        predicted = [r.task.detector_label for r in members]
        raw = _raw_counts(members, predicted)
        precision, recall = _weighted(members, predicted)
        result[key] = {**raw, "n": len(members), "precision_w": precision, "recall_w": recall}
    return result


def evaluate_labels(
    batch_id: UUID,
    tasks: Sequence[TaskRecord],
    labels: Sequence[LabelRecord],
    settings: LabellingSettings,
    *,
    threshold: float,
    seed: int,
    clients_by_channel: Mapping[str, Sequence[str]] | None = None,
) -> tuple[OutlierEvalResult, dict[UUID, GoldLabel]]:
    gold = resolve_gold(tasks, labels)
    agreement = label_agreement(batch_id, labels)
    rows = [
        GoldRow(task=task, gold=gold[task.task_id].gold == "outlier", weight=1 / task.inclusion_prob)
        for task in tasks
        if gold[task.task_id].gold in DEFINITE and task.inclusion_prob > 0
    ]
    methods = Counter(g.method for g in gold.values())
    excluded_share = methods["excluded"] / len(tasks) if tasks else 0.0
    warnings = []
    if agreement.cohen_kappa is not None and agreement.cohen_kappa < settings.kappa_gate:
        warnings.append(
            f"Labeller agreement kappa {agreement.cohen_kappa:.2f} is below {settings.kappa_gate}: revise the "
            "guidelines, recalibrate on new anchors and relabel a fresh 30 before trusting these numbers."
        )
    if excluded_share > settings.max_unsure_share:
        warnings.append(
            f"{excluded_share:.0%} of items were excluded as unsure by both labellers (limit "
            f"{settings.max_unsure_share:.0%}); revise the guidelines."
        )
    if methods["pending"]:
        warnings.append(f"{methods['pending']} items still need a second label or an adjudication round.")
    thresholds = threshold_grid(settings)
    predicted = [r.task.detector_label for r in rows]
    weights = [r.weight for r in rows]
    confusion = weighted_confusion([r.gold for r in rows], predicted, weights)
    raw = _raw_counts(rows, predicted)
    gold_array = np.array([r.gold for r in rows], dtype=bool)
    weight_array = np.array(weights, dtype=float)
    ratio_array = np.array([r.task.detector_ratio or 0.0 for r in rows], dtype=float)
    flag_array = np.array(predicted, dtype=bool)

    def precision_of(flags: np.ndarray):
        def stat(idx: np.ndarray) -> float | None:
            flagged = weight_array[idx] * flags[idx]
            total = flagged.sum()
            return float((flagged * gold_array[idx]).sum() / total) if total else None

        return stat

    def recall_of(flags: np.ndarray):
        def stat(idx: np.ndarray) -> float | None:
            positive = weight_array[idx] * gold_array[idx]
            total = positive.sum()
            return float((positive * flags[idx]).sum() / total) if total else None

        return stat

    statistics = {"precision": precision_of(flag_array), "recall": recall_of(flag_array)}
    for t in thresholds:
        statistics[f"p@{t}"] = precision_of(ratio_array >= t)
        statistics[f"r@{t}"] = recall_of(ratio_array >= t)
    boot = (
        item_bootstrap(
            statistics,
            len(rows),
            settings.bootstrap_resamples,
            np.random.default_rng(seed),
            strata=[r.task.stratum for r in rows],
        )
        if rows
        else {}
    )
    curve = tuple(
        point.model_copy(
            update={
                "precision_ci": boot[f"p@{point.threshold}"].ci if boot else None,
                "recall_ci": boot[f"r@{point.threshold}"].ci if boot else None,
            }
        )
        for point in _points(rows, thresholds)
    )
    chosen = choose_threshold(curve, settings.min_precision)
    reason_codes: dict[str, Counter[str]] = {"false_positive": Counter(), "false_negative": Counter()}
    by_task_labels: dict[UUID, list[LabelRecord]] = defaultdict(list)
    for label in labels:
        by_task_labels[label.task_id].append(label)
    for row, flagged in zip(rows, predicted, strict=True):
        kind = (
            "false_positive"
            if flagged and not row.gold
            else "false_negative"
            if row.gold and not flagged
            else None
        )
        if kind:
            for label in by_task_labels[row.task.task_id]:
                reason_codes[kind].update(label.reason_codes)
    clients = clients_by_channel or {}
    result = OutlierEvalResult(
        batch_id=batch_id,
        detector_version=tasks[0].detector_version if tasks else "",
        threshold=threshold,
        n_tasks=len(tasks),
        n_gold=len(rows),
        gold_methods=dict(sorted(methods.items())),
        excluded_share=excluded_share,
        agreement=agreement,
        confusion_weighted={"tp": confusion.tp, "fp": confusion.fp, "fn": confusion.fn, "tn": confusion.tn},
        confusion_raw=raw,
        precision_w=confusion.precision,
        recall_w=confusion.recall,
        f1_w=confusion.f_beta(1.0),
        f05_w=confusion.f_beta(0.5),
        precision_ci=boot["precision"].ci if boot else None,
        recall_ci=boot["recall"].ci if boot else None,
        precision_wilson_raw=wilson(raw["tp"], raw["tp"] + raw["fp"]) if raw["tp"] + raw["fp"] else None,
        recall_wilson_raw=wilson(raw["tp"], raw["tp"] + raw["fn"]) if raw["tp"] + raw["fn"] else None,
        by_stratum=_breakdown(rows, lambda r: [r.task.stratum]),
        by_client=_breakdown(rows, lambda r: list(clients.get(r.task.channel_id or "", ()))),
        reason_codes={kind: dict(sorted(counter.items())) for kind, counter in reason_codes.items()},
        curve=curve,
        recommended_threshold=chosen,
        loco_optimism_f05=loco_optimism(rows, thresholds, chosen, settings.min_precision) if chosen else None,
        warnings=tuple(warnings),
    )
    return result, gold
