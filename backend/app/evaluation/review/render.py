from collections.abc import Sequence

from app.evaluation.reporting.markdown import estimate_with_ci, num, pct, table
from app.evaluation.review.models import GateProposal, ReviewSummary
from app.evaluation.review.rubric import RUBRIC


def render_review_summary(summary: ReviewSummary, proposals: Sequence[GateProposal] = ()) -> str:
    verdict = "PASS" if summary.passed else "NOT PASSED"
    lines = [
        "# Human review of recommendation cards",
        "",
        f"Batch `{summary.batch_id}` · {summary.n_items} cards rated by {summary.n_raters} raters · "
        f"{summary.n_decoys} blinded decoys",
        "",
        f"**{verdict}** — overall mean {estimate_with_ci(summary.overall_mean, summary.overall_ci)} "
        f"(target ≥ 4.0 and every criterion ≥ 3.5; lower bound passes: "
        f"{'yes' if summary.lower_bound_passes else 'no'}).",
        "",
        f"Median item score {num(summary.overall_median)} · items with mean ≥ 4: {pct(summary.share_items_ge4)} · "
        f"would ship: {pct(summary.would_ship_share)}",
        "",
    ]
    if summary.warnings:
        lines += ["## Warnings", "", *[f"- {w}" for w in summary.warnings], ""]
    lines += [
        "## By criterion",
        "",
        table(
            [
                "criterion",
                "mean [95% CI]",
                "Krippendorff α (ordinal)",
                "weighted κ (quadratic)",
                "decoy mean",
            ],
            [
                [
                    RUBRIC[name][0],
                    estimate_with_ci(stat.mean, stat.ci),
                    num(stat.alpha_ordinal),
                    num(stat.weighted_kappa),
                    num(stat.decoy_mean),
                ]
                for name, stat in summary.by_criterion.items()
            ],
        ),
        "",
        "## Decoys",
        "",
    ]
    if summary.decoy_gap is None:
        lines.append("No decoy ratings yet.")
    else:
        lines.append(
            f"Real minus decoy, overall: {num(summary.decoy_gap['overall'])} (per-rater z-scores: "
            f"{num(summary.decoy_gap_z)}); informative: {'yes' if summary.decoys_informative else 'no'}."
        )
    lines += [
        "",
        "## Raters",
        "",
        table(
            ["rater (pseudonymous id)", "cards", "mean", "sd"],
            [[r.rater_id[:8], r.n_ratings, num(r.mean), num(r.sd)] for r in summary.raters],
        ),
        "",
        "## By type, client and lens",
        "",
        table(
            ["group", "mean item score"],
            [
                *[[f"type: {k}", num(v)] for k, v in summary.by_type.items()],
                *[[f"client: {k[:8]}", num(v)] for k, v in summary.by_client.items()],
                *[[f"lens: {k}", num(v)] for k, v in summary.by_lens.items()],
            ],
        ),
        "",
        "## Gate tuning proposals (exploratory, need approval)",
        "",
    ]
    if proposals:
        lines.append(
            table(
                ["feature", "ρ", "proposed", "kept", "kept mean", "rationale"],
                [
                    [
                        p.feature,
                        num(p.spearman_rho),
                        f"{'≥' if p.direction == 'higher_is_better' else '≤'} {p.proposed_threshold:g}",
                        pct(p.retained_share),
                        num(p.retained_mean_rating),
                        p.rationale,
                    ]
                    for p in proposals
                ],
            )
        )
    else:
        lines.append("No threshold keeps ≥ 70% of cards at a mean of 4.0 or more; nothing proposed.")
    return "\n".join(lines) + "\n"
