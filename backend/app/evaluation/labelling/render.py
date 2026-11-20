from app.evaluation.labelling.models import OutlierEvalResult
from app.evaluation.reporting.markdown import estimate_with_ci, interval, num, pct, table


def render_outlier_eval(result: OutlierEvalResult) -> str:
    agreement = result.agreement
    lines = [
        "# Outlier detector precision and recall",
        "",
        f"Batch `{result.batch_id}` · detector `{result.detector_version}` · threshold θ = {result.threshold:g}",
        "",
        "Weighted figures use Horvitz-Thompson weights (1 / inclusion probability); intervals are 95% "
        "stratified item-bootstrap intervals. Raw Wilson intervals on unweighted counts are a cross-check.",
        "",
    ]
    if result.warnings:
        lines += ["## Warnings", "", *[f"- {w}" for w in result.warnings], ""]
    lines += [
        "## Headline",
        "",
        table(
            ["metric", "weighted [95% CI]", "raw Wilson 95%"],
            [
                [
                    "precision",
                    estimate_with_ci(result.precision_w, result.precision_ci, as_pct=True),
                    interval(result.precision_wilson_raw, as_pct=True),
                ],
                [
                    "recall",
                    estimate_with_ci(result.recall_w, result.recall_ci, as_pct=True),
                    interval(result.recall_wilson_raw, as_pct=True),
                ],
                ["F1", num(result.f1_w), ""],
                ["F0.5", num(result.f05_w), ""],
            ],
        ),
        "",
        f"Gold labels: {result.n_gold} of {result.n_tasks} tasks "
        f"({', '.join(f'{k} {v}' for k, v in result.gold_methods.items())}); excluded share "
        f"{pct(result.excluded_share)}.",
        "",
        "## Labeller agreement",
        "",
        table(
            ["items", "percent agreement", "Cohen's κ", "Gwet's AC1", "unsure per labeller"],
            [
                [
                    agreement.n_items,
                    pct(agreement.percent_agreement),
                    num(agreement.cohen_kappa),
                    num(agreement.gwet_ac1),
                    ", ".join(f"{k[:8]}: {v}" for k, v in agreement.n_unsure.items()) or "n/a",
                ]
            ],
        ),
        "",
        "Interpretation follows Landis & Koch (1977); AC1 is shown because κ is depressed when outliers are rare.",
        "",
        "## Confusion matrix",
        "",
        table(
            ["", "raw", "weighted"],
            [
                [key.upper(), result.confusion_raw[key], num(result.confusion_weighted[key], 1)]
                for key in result.confusion_raw
            ],
        ),
        "",
        "## Threshold sweep",
        "",
        "Rule: maximise F0.5 subject to weighted precision ≥ 0.80; ties go to the higher θ (99 C12). A new θ is a "
        "proposal for 06's config and applies only to freezes taken after approval.",
        "",
        table(
            ["θ", "precision", "recall", "F0.5", "F1", "precision 95% CI", "recall 95% CI"],
            [
                [
                    f"{p.threshold:g}" + (" ←" if p.threshold == result.recommended_threshold else ""),
                    pct(p.precision_w),
                    pct(p.recall_w),
                    num(p.f05_w),
                    num(p.f1_w),
                    interval(p.precision_ci, as_pct=True),
                    interval(p.recall_ci, as_pct=True),
                ]
                for p in result.curve
            ],
        ),
        "",
        f"Recommended θ: **{num(result.recommended_threshold)}** · leave-one-channel-out optimism of F0.5: "
        f"{num(result.loco_optimism_f05, 3)}",
        "",
        "## By stratum",
        "",
        _groups(result.by_stratum),
        "",
    ]
    if result.by_client:
        lines += ["## By client", "", _groups(result.by_client), ""]
    lines += [
        "## Reason codes on errors",
        "",
        table(
            ["error", "reason codes"],
            [
                [kind.replace("_", " "), ", ".join(f"{code} {n}" for code, n in codes.items()) or "none"]
                for kind, codes in result.reason_codes.items()
            ],
        ),
        "",
        "Detector variants (mean vs median baseline, pooled vs format-split) are not scored in this report.",
    ]
    return "\n".join(lines) + "\n"


def _groups(groups: dict[str, dict[str, float | int | None]]) -> str:
    return table(
        ["group", "n", "TP", "FP", "FN", "TN", "precision", "recall"],
        [
            [
                key,
                values["n"],
                values["tp"],
                values["fp"],
                values["fn"],
                values["tn"],
                pct(values["precision_w"]),
                pct(values["recall_w"]),
            ]
            for key, values in groups.items()
        ],
    )
