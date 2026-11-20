"""arm metrics, paired cluster-bootstrap inference and the verdict (plan 12 §5.1.2 step 7, §5.1.3, §5.1.4)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.domain.confidence import ConfidenceLabel
from app.evaluation.backtest.frame import ClientEvaluation, Frame, build_frame, geo_mean, mean_log, rate
from app.evaluation.backtest.models import (
    ArmComparison,
    ArmMetrics,
    BacktestConfig,
    BacktestReport,
    Verdict,
)
from app.evaluation.stats.bootstrap import BootstrapEstimate, stratified_cluster_bootstrap
from app.evaluation.stats.inference import empirical_p, holm, percentile_of
from app.evaluation.stats.proportions import minimum_detectable_lift, wilson

CONFIDENCE_ORDER = tuple(
    label.value for label in (ConfidenceLabel.high, ConfidenceLabel.medium, ConfidenceLabel.low)
)
LOW_DISCRIMINATION_JACCARD = 0.8
FIXED_LIMITATIONS = (
    "Outcomes are views at day 7 from snapshots of videos tracked from upload; nothing is extrapolated.",
    "Videos removed by their owners after T are excluded; they skew towards underperformers.",
    "Post-T topic matches come from the live topic engine using the T-version topic list.",
    "news_opportunity and description_refresh cards are not backtested (no reproducible as-of-T inputs).",
    "The retrospective proxy backtest (Mode B) and the gate ablation are not part of this report.",
)


@dataclass(frozen=True)
class ScoringSettings:
    resamples: int
    alpha: float
    seed: int
    floors: dict[str, int]
    few_clusters: int
    max_missing_share: float


def scoring_settings(configs: Sequence[BacktestConfig], few_clusters: int) -> ScoringSettings:
    first = configs[0]
    return ScoringSettings(
        resamples=first.bootstrap_resamples,
        alpha=first.alpha,
        seed=first.seed,
        floors=dict(first.floors),
        few_clusters=few_clusters,
        max_missing_share=first.max_missing_share,
    )


def _arm_bootstrap(
    frame: Frame, scope_rows: np.ndarray, settings: ScoringSettings
) -> dict[str, BootstrapEstimate]:
    o, lr = frame.outlier.astype(float), frame.log_ratio
    sub = {name: frame.mask(name)[scope_rows] for name in ("system", "copy_top", "base")}
    out, logs = o[scope_rows], lr[scope_rows]

    def m1(arm: str):
        return lambda idx: rate(out, sub[arm], idx)

    def diff(a: str, b: str):
        def stat(idx: np.ndarray) -> float | None:
            left, right = rate(out, sub[a], idx), rate(out, sub[b], idx)
            return None if left is None or right is None else left - right

        return stat

    def risk_ratio(a: str, b: str):
        def stat(idx: np.ndarray) -> float | None:
            left, right = rate(out, sub[a], idx), rate(out, sub[b], idx)
            return None if left is None or not right else left / right

        return stat

    def log_diff(a: str, b: str):
        def stat(idx: np.ndarray) -> float | None:
            left, right = mean_log(logs, sub[a], idx), mean_log(logs, sub[b], idx)
            return None if left is None or right is None else left - right

        return stat

    statistics = {
        "m1_system": m1("system"),
        "m1_copy_top": m1("copy_top"),
        "m1_base": m1("base"),
        "m2_system": lambda idx: geo_mean(logs, sub["system"], idx),
        "m2_copy_top": lambda idx: geo_mean(logs, sub["copy_top"], idx),
        "m2_base": lambda idx: geo_mean(logs, sub["base"], idx),
        "lift_system": risk_ratio("system", "base"),
        "lift_copy_top": risk_ratio("copy_top", "base"),
        "diff_system_copy_top": diff("system", "copy_top"),
        "rr_system_copy_top": risk_ratio("system", "copy_top"),
        "logdiff_system_copy_top": log_diff("system", "copy_top"),
        "diff_system_base": diff("system", "base"),
        "logdiff_system_base": log_diff("system", "base"),
    }
    return stratified_cluster_bootstrap(
        statistics,
        list(frame.clients[scope_rows]),
        list(frame.channels[scope_rows]),
        settings.resamples,
        np.random.default_rng(settings.seed),
        settings.alpha,
    )


def _arm_metrics(
    frame: Frame, scope: str, rows: np.ndarray, boot: dict[str, BootstrapEstimate], settings: ScoringSettings
) -> list[ArmMetrics]:
    metrics = []
    clients = set(frame.clients[rows])
    in_scope = set(rows.tolist())
    for arm in ("system", "copy_top", "base"):
        mask = frame.mask(arm)[rows]
        matched = rows[mask]
        n_outliers = int(frame.outlier[matched].sum())
        specs = [m for m in frame.spec_matches if m.item.arm == arm and m.client_id in clients]
        scoped = [[r for r in m.rows if r in in_scope] for m in specs]
        tested = [hits for hits in scoped if hits]
        channels = len(set(frame.channels[matched]))
        metrics.append(
            ArmMetrics(
                arm=arm,
                scope=scope,
                n_specs=len(specs),
                n_specs_matched=len(tested),
                n_specs_untestable=len(specs) - len(tested),
                n_videos_matched=int(mask.sum()),
                n_channels_matched=channels,
                coverage=float(mask.mean()) if rows.size else None,
                outlier_rate=boot[f"m1_{arm}"].estimate,
                outlier_rate_ci=boot[f"m1_{arm}"].ci,
                outlier_rate_wilson=wilson(n_outliers, int(mask.sum()), settings.alpha)
                if mask.any()
                else None,
                geo_mean_ratio=boot[f"m2_{arm}"].estimate,
                geo_mean_ratio_ci=boot[f"m2_{arm}"].ci,
                spec_hit_rate=(
                    sum(bool(frame.outlier[hits].any()) for hits in tested) / len(tested) if tested else None
                ),
                lift_vs_base=boot[f"lift_{arm}"].estimate if arm != "base" else 1.0,
                lift_vs_base_ci=boot[f"lift_{arm}"].ci if arm != "base" else None,
                few_clusters=channels < settings.few_clusters,
            )
        )
    return metrics


def _random_metrics(frame: Frame, rows: np.ndarray, scope: str) -> tuple[ArmMetrics | None, np.ndarray]:
    if frame.random.shape[0] == 0:
        return None, np.zeros(0)
    masks = frame.random[:, rows]
    counts = masks.sum(axis=1)
    rates = np.asarray([frame.outlier[rows][m].mean() if m.any() else np.nan for m in masks], dtype=float)
    valid = rates[~np.isnan(rates)]
    logs = [frame.log_ratio[rows][m].mean() for m in masks if m.any()]
    metric = ArmMetrics(
        arm="random",
        scope=scope,
        n_specs=0,
        n_specs_matched=0,
        n_specs_untestable=0,
        n_videos_matched=int(round(float(counts.mean()))),
        n_channels_matched=int(round(float(np.mean([len(set(frame.channels[rows][m])) for m in masks])))),
        coverage=float(counts.mean() / rows.size) if rows.size else None,
        outlier_rate=float(valid.mean()) if valid.size else None,
        outlier_rate_ci=(float(np.quantile(valid, 0.025)), float(np.quantile(valid, 0.975)))
        if valid.size
        else None,
        outlier_rate_wilson=None,
        geo_mean_ratio=float(np.exp(np.mean(logs))) if logs else None,
        geo_mean_ratio_ci=None,
        spec_hit_rate=None,
        lift_vs_base=None,
        lift_vs_base_ci=None,
    )
    return metric, valid


def _comparisons(
    boot: dict[str, BootstrapEstimate], random_rates: np.ndarray, system_rate: float | None
) -> tuple[list[ArmComparison], float | None]:
    primary = boot["diff_system_copy_top"]
    comparisons = [
        ArmComparison(
            scope="pooled",
            a="system",
            b="copy_top",
            metric="outlier_rate_diff",
            estimate=primary.estimate,
            ci=primary.ci,
            p_value=primary.share_at_or_below(0.0),
            method="stratified cluster bootstrap (one-sided)",
            primary=True,
        ),
        ArmComparison(
            scope="pooled",
            a="system",
            b="copy_top",
            metric="risk_ratio",
            estimate=boot["rr_system_copy_top"].estimate,
            ci=boot["rr_system_copy_top"].ci,
            p_value=None,
            method="stratified cluster bootstrap",
            primary=True,
        ),
    ]
    percentile = None
    if random_rates.size and system_rate is not None:
        percentile = percentile_of(system_rate, random_rates)
        comparisons.append(
            ArmComparison(
                scope="pooled",
                a="system",
                b="random",
                metric="outlier_rate_vs_random",
                estimate=system_rate - float(random_rates.mean()),
                ci=None,
                p_value=empirical_p(system_rate, random_rates),
                method=f"empirical over {random_rates.size} random replicates (phipson & smyth 2010)",
                primary=True,
            )
        )
    secondary = [
        ("system", "copy_top", "log_ratio_diff", "logdiff_system_copy_top"),
        ("system", "base", "outlier_rate_diff", "diff_system_base"),
        ("system", "base", "log_ratio_diff", "logdiff_system_base"),
    ]
    raw = [boot[key].share_at_or_below(0.0) for *_, key in secondary]
    adjusted = holm([p if p is not None else 1.0 for p in raw])
    for (a, b, metric, key), p, p_adj in zip(secondary, raw, adjusted, strict=True):
        comparisons.append(
            ArmComparison(
                scope="pooled",
                a=a,
                b=b,
                metric=metric,
                estimate=boot[key].estimate,
                ci=boot[key].ci,
                p_value=p,
                p_adjusted=p_adj if p is not None else None,
                method="stratified cluster bootstrap (one-sided), holm-adjusted, exploratory",
            )
        )
    return comparisons, percentile


def _verdict(
    frame: Frame,
    arms: list[ArmMetrics],
    comparisons: list[ArmComparison],
    missing_share: float,
    invalid: list[str],
    settings: ScoringSettings,
) -> tuple[Verdict, list[str]]:
    reasons = list(invalid)
    if missing_share > settings.max_missing_share:
        reasons.append(f"{missing_share:.0%} of outcome videos lack a day-a measurement")
    if reasons:
        return "invalid", reasons
    pooled = {m.arm: m for m in arms if m.scope == "pooled"}
    floors = settings.floors
    short = []
    if frame.n < floors["min_universe_videos_pooled"]:
        short.append(f"{frame.n} complete post-T videos (need {floors['min_universe_videos_pooled']})")
    for arm in ("system", "copy_top"):
        if pooled[arm].n_videos_matched < floors["min_matched_videos_pooled"]:
            short.append(
                f"{pooled[arm].n_videos_matched} videos matched {arm} (need {floors['min_matched_videos_pooled']})"
            )
    channels = len(set(frame.channels))
    if channels < floors["min_channels_pooled"]:
        short.append(f"{channels} channels (need {floors['min_channels_pooled']})")
    if short:
        return "inconclusive_underpowered", short
    primary = comparisons[0]
    random_p = next((c.p_value for c in comparisons if c.b == "random"), None)
    if primary.ci is None:
        return "inconclusive_underpowered", ["the primary difference could not be estimated"]
    if primary.ci[0] > 0 and random_p is not None and random_p < settings.alpha:
        return "supported", []
    if primary.ci[1] <= 0:
        return "not_supported", []
    return "inconclusive_underpowered", [
        "the primary interval spans zero or the random-arm test is not significant"
    ]


def _calibration(frame: Frame) -> dict:
    by_label: dict[str, dict[str, float | int | None]] = {}
    for label in CONFIDENCE_ORDER:
        rows = sorted(
            {
                r
                for m in frame.spec_matches
                if m.item.arm == "system" and m.item.confidence == label
                for r in m.rows
            }
        )
        by_label[label] = {
            "videos": len(rows),
            "outlier_rate": float(frame.outlier[rows].mean()) if rows else None,
        }
    present = [
        by_label[label]["outlier_rate"]
        for label in CONFIDENCE_ORDER
        if by_label[label]["outlier_rate"] is not None
    ]
    monotone = all(a >= b for a, b in zip(present, present[1:], strict=False)) if len(present) > 1 else None
    inside = total = 0
    for match in frame.spec_matches:
        item = match.item
        if item.arm != "system" or item.expected_low is None or item.expected_high is None:
            continue
        for row in match.rows:
            ratio = frame.videos[row].ratio
            total += 1
            inside += ratio is not None and item.expected_low <= ratio <= item.expected_high
    return {
        "by_confidence": by_label,
        "confidence_monotone": monotone,
        "range_coverage": inside / total if total else None,
        "range_coverage_n": total,
        "range_coverage_target": "50-80% (indicative interquartile-style range)",
    }


def _lens_split(frame: Frame) -> dict[str, dict[str, float | int | None]]:
    groups = {"conversion": set(), "views_only": set()}
    for match in frame.spec_matches:
        if match.item.arm != "system":
            continue
        lenses = {lens.value for lens in match.item.spec.lens}
        groups["conversion" if "conversion" in lenses else "views_only"].update(match.rows)
    split = {}
    for name, rows in groups.items():
        ordered = sorted(rows)
        split[name] = {
            "videos": len(ordered),
            "outlier_rate": float(frame.outlier[ordered].mean()) if ordered else None,
            "geo_mean_ratio": float(np.exp(frame.log_ratio[ordered].mean())) if ordered else None,
        }
    return split


def _within_channel(frame: Frame) -> dict[str, float | int | None]:
    """mantel-haenszel risk ratio of matched vs unmatched videos, stratified by channel (removes channel mix)."""
    numerator = denominator = 0.0
    strata = 0
    for channel in sorted(set(frame.channels)):
        rows = frame.channels == channel
        matched, unmatched = rows & frame.system, rows & ~frame.system
        n1, n0 = int(matched.sum()), int(unmatched.sum())
        if not n1 or not n0:
            continue
        total = n1 + n0
        numerator += frame.outlier[matched].sum() * n0 / total
        denominator += frame.outlier[unmatched].sum() * n1 / total
        strata += 1
    return {
        "channels_with_contrast": strata,
        "mantel_haenszel_risk_ratio": numerator / denominator if denominator else None,
    }


def _overlap(frame: Frame) -> dict[str, float | bool | None]:
    union = frame.system | frame.copy_top
    jaccard = float((frame.system & frame.copy_top).sum() / union.sum()) if union.any() else None
    return {
        "jaccard_system_copy_top": jaccard,
        "low_discrimination": None if jaccard is None else jaccard > LOW_DISCRIMINATION_JACCARD,
    }


def _sensitivity(frame: Frame) -> dict[str, float | int | None]:
    # 06's own day-a outlier flag at E (baseline recomputed with post-T videos) instead of the frozen T baseline
    every = np.arange(frame.n)
    return {
        f"detector_outlier_rate_{arm}": rate(frame.detector_outlier.astype(float), frame.mask(arm), every)
        for arm in ("system", "copy_top", "base")
    }


def _spec_table(frame: Frame) -> list[dict]:
    table = []
    for match in frame.spec_matches:
        spec = match.item.spec
        table.append(
            {
                "arm": match.item.arm,
                "spec_id": spec.spec_id,
                "type": spec.rec_type.value,
                "specificity": spec.specificity,
                "confidence": match.item.confidence,
                "matched": len(match.rows),
                "outliers": int(frame.outlier[match.rows].sum()) if match.rows else 0,
            }
        )
    return table


def score(
    evaluations: Sequence[ClientEvaluation],
    configs: Sequence[BacktestConfig],
    freezes: list[dict],
    settings: ScoringSettings,
    generated_at: datetime,
    frame: Frame | None = None,
) -> BacktestReport:
    frame = frame or build_frame(evaluations)
    every = np.arange(frame.n)
    boot = _arm_bootstrap(frame, every, settings)
    arms = _arm_metrics(frame, "pooled", every, boot, settings)
    random_metric, random_rates = _random_metrics(frame, every, "pooled")
    if random_metric is not None:
        arms.append(random_metric)
    for evaluation in evaluations:
        rows = np.flatnonzero(frame.clients == evaluation.client_id)
        system_rows = int(frame.system[rows].sum())
        copy_rows = int(frame.copy_top[rows].sum())
        floors = settings.floors
        if (
            rows.size >= floors["min_universe_videos_client"]
            and min(system_rows, copy_rows) >= floors["min_matched_videos_client"]
        ):
            arms += _arm_metrics(
                frame, f"client:{evaluation.client_id}", rows, _arm_bootstrap(frame, rows, settings), settings
            )
    system_rate = boot["m1_system"].estimate
    comparisons, percentile = _comparisons(boot, random_rates, system_rate)
    all_outcomes = [o for e in evaluations for o in e.outcomes]
    missingness = {
        status: sum(o.status == status for o in all_outcomes)
        for status in ("complete", "pending", "missing_snapshot", "deleted", "no_baseline")
    }
    scored = len(all_outcomes) - missingness["deleted"]
    missing_share = (missingness["missing_snapshot"] + missingness["pending"]) / scored if scored else 0.0
    invalid = [reason for e in evaluations for reason in e.invalid_reasons]
    verdict, reasons = _verdict(frame, arms, comparisons, missing_share, invalid, settings)
    pooled = {m.arm: m for m in arms if m.scope == "pooled"}
    base_rate = pooled["copy_top"].outlier_rate or pooled["base"].outlier_rate
    matched_n = min(pooled["system"].n_videos_matched, pooled["copy_top"].n_videos_matched)
    mde = {
        "base_rate": base_rate,
        "matched_per_arm": float(matched_n),
        "min_detectable_lift": minimum_detectable_lift(base_rate, matched_n, settings.alpha)
        if base_rate
        else None,
        "min_detectable_lift_vs_known_base": minimum_detectable_lift(
            pooled["base"].outlier_rate or 0.0,
            pooled["system"].n_videos_matched,
            settings.alpha,
            two_sample=False,
        ),
    }
    limitations = list(FIXED_LIMITATIONS) + reasons
    if frame.n and not frame.outlier.any():
        limitations.append(
            "No post-T video was an outlier in any arm; the geometric-mean ratio is the exploratory read."
        )
    if missingness["no_baseline"]:
        limitations.append(
            f"{missingness['no_baseline']} videos came from channels without a fair baseline at T."
        )
    limitations += [note for e in evaluations for note in e.notes]
    limitations += frame.unmatchable
    abstentions = [e.client_id for e in evaluations if not any(s.arm == "system" for s in e.specs)]
    primary = comparisons[0]
    return BacktestReport(
        eval_ids=[e.eval_id for e in evaluations],
        configs=list(configs),
        freezes=freezes,
        verdict=verdict,
        headline=_headline(verdict, primary, mde),
        arms=arms,
        comparisons=comparisons,
        random_percentile=percentile,
        mde=mde,
        calibration=_calibration(frame),
        lens_split=_lens_split(frame),
        within_channel=_within_channel(frame),
        overlap=_overlap(frame),
        missingness=missingness,
        abstentions=abstentions,
        sensitivity=_sensitivity(frame),
        limitations=limitations,
        spec_table=_spec_table(frame),
        generated_at=generated_at,
    )


def _headline(verdict: Verdict, primary: ArmComparison, mde: dict) -> str:
    estimate = "n/a" if primary.estimate is None else f"{primary.estimate:+.1%}"
    interval = "n/a" if primary.ci is None else f"[{primary.ci[0]:+.1%}, {primary.ci[1]:+.1%}]"
    lift = mde.get("min_detectable_lift")
    detectable = "n/a" if lift is None else f"{lift:.1f}x"
    return (
        f"Verdict: {verdict.replace('_', ' ')}. Outlier rate of videos following our cards minus "
        f"copy-the-top: {estimate} {interval}; minimum detectable lift at this sample size: {detectable}."
    )
