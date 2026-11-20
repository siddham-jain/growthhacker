from app.evaluation.backtest.models import ArmMetrics, BacktestReport
from app.evaluation.reporting.markdown import estimate_with_ci, num, pct, table

ARM_LABELS = {
    "system": "GrowthHacker cards",
    "copy_top": "Copy the most-viewed recent video",
    "random": "Random patterns",
    "base": "Niche base rate (all post-T videos)",
}


def _arm_row(arm: ArmMetrics) -> list[str]:
    return [
        ARM_LABELS.get(arm.arm, arm.arm),
        str(arm.n_specs),
        str(arm.n_videos_matched),
        str(arm.n_channels_matched) + (" (few clusters)" if arm.few_clusters else ""),
        pct(arm.coverage),
        estimate_with_ci(arm.outlier_rate, arm.outlier_rate_ci or arm.outlier_rate_wilson, as_pct=True),
        estimate_with_ci(arm.geo_mean_ratio, arm.geo_mean_ratio_ci),
        estimate_with_ci(arm.lift_vs_base, arm.lift_vs_base_ci),
        pct(arm.spec_hit_rate),
    ]


ARM_HEADERS = (
    "Arm",
    "Specs",
    "Matched videos",
    "Channels",
    "Coverage",
    "Outlier rate [95% CI]",
    "Geo-mean ratio [CI]",
    "Lift vs base [CI]",
    "Spec hit rate",
)


def render_backtest(report: BacktestReport) -> str:
    config = report.configs[0] if report.configs else None
    lines = ["# Backtest report", "", f"> {report.headline}", ""]
    if config is not None:
        lines += [
            "## Setup",
            "",
            table(
                ("Item", "Value"),
                [
                    ("Evaluations", ", ".join(str(e) for e in report.eval_ids)),
                    ("Clients", len(report.configs)),
                    ("Frozen at (T)", ", ".join(c.as_of.isoformat() for c in report.configs)),
                    ("Reference window W (days)", config.window_days),
                    ("Publish horizon H (days)", config.horizon_days),
                    ("Outcome age a (days)", config.outcome_age_days),
                    ("Outlier threshold θ (frozen at T)", config.outlier_threshold),
                    ("Random replicates", config.random_replicates),
                    ("Bootstrap resamples", config.bootstrap_resamples),
                    ("Config hashes", ", ".join(c.canonical_hash()[:12] for c in report.configs)),
                    ("Code version", config.code_version),
                    ("Point-in-time level", ", ".join(f["pit_level"] for f in report.freezes)),
                ],
            ),
            "",
        ]
    lines += [
        "## Arms (pooled)",
        "",
        table(ARM_HEADERS, [_arm_row(a) for a in report.arms if a.scope == "pooled"]),
        "",
    ]
    clients = [a for a in report.arms if a.scope.startswith("client:")]
    if clients:
        lines += [
            "## Arms per client",
            "",
            table(("Scope", *ARM_HEADERS), [[a.scope, *_arm_row(a)] for a in clients]),
            "",
        ]
    lines += [
        "## Comparisons",
        "",
        table(
            ("Comparison", "Metric", "Estimate", "95% CI", "p", "p (Holm)", "Method"),
            [
                (
                    f"{c.a} vs {c.b}" + (" (primary)" if c.primary else ""),
                    c.metric,
                    num(c.estimate, 3),
                    "n/a" if c.ci is None else f"[{c.ci[0]:.3f}, {c.ci[1]:.3f}]",
                    num(c.p_value, 4),
                    num(c.p_adjusted, 4),
                    c.method,
                )
                for c in report.comparisons
            ],
        ),
        "",
        f"System percentile within the random-arm distribution: {pct(report.random_percentile)}",
        "",
        "## Power",
        "",
        table(("Item", "Value"), [(key, num(value, 3)) for key, value in report.mde.items()]),
        "",
        "## Calibration, lens split and robustness",
        "",
        table(
            ("Confidence label", "Videos", "Outlier rate"),
            [
                (label, values["videos"], pct(values["outlier_rate"]))
                for label, values in report.calibration["by_confidence"].items()
            ],
        ),
        "",
        f"Outlier rate falls with confidence (High ≥ Medium ≥ Low): {report.calibration['confidence_monotone']}. "
        f"Range coverage: {pct(report.calibration['range_coverage'])} of "
        f"{report.calibration['range_coverage_n']} matched videos (target "
        f"{report.calibration['range_coverage_target']}).",
        "",
        table(
            ("Lens", "Videos", "Outlier rate", "Geo-mean ratio"),
            [
                (lens, values["videos"], pct(values["outlier_rate"]), num(values["geo_mean_ratio"]))
                for lens, values in report.lens_split.items()
            ],
        ),
        "",
        table(
            ("Check", "Value"),
            [
                *(
                    (key, num(value, 3) if isinstance(value, float) else value)
                    for key, value in report.within_channel.items()
                ),
                *(
                    (key, num(value, 3) if isinstance(value, float) else value)
                    for key, value in report.overlap.items()
                ),
                *(
                    (key, pct(value) if isinstance(value, float) else value)
                    for key, value in report.sensitivity.items()
                ),
            ],
        ),
        "",
        "## Missingness and exclusions",
        "",
        table(("Status", "Videos"), sorted(report.missingness.items())),
        "",
    ]
    if report.abstentions:
        lines += [f"Engine abstained (no gated cards at T) for: {', '.join(report.abstentions)}.", ""]
    lines += ["## Limitations", "", *(f"- {item}" for item in report.limitations), ""]
    if report.spec_table:
        # aggregates only: video ids are api data and stay in the expiring eval_outcome_video rows
        lines += [
            "## Appendix: specs",
            "",
            table(
                ("Arm", "Spec", "Type", "Specificity", "Confidence", "Matched", "Outliers"),
                [
                    (
                        row["arm"],
                        row["spec_id"],
                        row["type"],
                        row["specificity"],
                        row["confidence"],
                        row["matched"],
                        row["outliers"],
                    )
                    for row in report.spec_table
                ],
            ),
            "",
        ]
    lines.append("Calculated by GrowthHacker from YouTube Data API counts; not a YouTube metric.")
    return "\n".join(lines)
